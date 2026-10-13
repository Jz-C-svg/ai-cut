# -*- coding: utf-8 -*-
"""画面干净检查（cleancheck）：单帧画面整洁度评分技能

纯本地 OpenCV 实现（免费、无网络、无付费 API），用于评估画面是否"干净"：
- 背景杂乱度（边缘密度 / 纹理复杂度 / 背景区域角点）
- 叠影重影（清晰度 / 多重边缘 / 半透明残影近似）
- 主体占比与构图（中心显著性主体）
- 字幕遮挡（底部字幕区文字密度与主体列重叠）
- 角落异物（四角水印/图标/界面元素）

输出：0-100 干净度分 + 各维度 0-10 分 + 问题清单 + 处理建议。
供 agent 评审链路（review_film / Editor-Critic）与导出报告调用。
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError as _e:  # pragma: no cover
    raise ImportError("画面干净检查需要 opencv-python-headless："
                      "pip install opencv-python-headless") from _e

__all__ = ["check_frame", "check_image_file", "CLEAN_LEVELS"]


CLEAN_LEVELS = [
    (85, "干净", "画面整洁，主体突出，可直接发布"),
    (70, "良好", "轻微杂乱，建议微调后发布"),
    (55, "一般", "存在明显干扰项，建议处理后发布"),
    (0, "不干净", "干扰严重，强烈建议重拍或大幅处理"),
]


def _clamp(v: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return float(max(lo, min(hi, v)))


def _laplacian_sharpness(gray: np.ndarray) -> float:
    """拉普拉斯方差：越大越清晰（对重影/模糊敏感）"""
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return float(lap.var())


def _edge_density(gray: np.ndarray, low=60, high=160) -> float:
    edges = cv2.Canny(gray, low, high)
    return float(edges.mean() / 255.0)


def _corner_density(gray: np.ndarray) -> float:
    """角点密度：背景纹理/杂物多则高"""
    g = cv2.GaussianBlur(gray, (3, 3), 0)
    corners = cv2.goodFeaturesToTrack(g, maxCorners=300, qualityLevel=0.01,
                                      minDistance=5)
    if corners is None:
        return 0.0
    h, w = gray.shape
    return float(len(corners) / (w * h) * 1e4)  # 每万像素角点数


def _subject_region(gray: np.ndarray, edge_ratio: float):
    """中心显著性主体估计：中心 45% 区域的高细节连通块。

    返回 (subject_ratio, center_offset_x, center_offset_y)：
    subject_ratio  主体占画面比例（0~1）
    center_offset  主体质心偏离画面中心的归一化距离（0~1）
    """
    h, w = gray.shape
    cw, ch = int(w * 0.45), int(h * 0.45)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    center = gray[y0:y0 + ch, x0:x0 + cw]

    # 中心区细节掩码：局部梯度能量高于全图中位数即视为"主体像素"
    gy = np.abs(np.gradient(center.astype(np.float32), axis=0))
    gx = np.abs(np.gradient(center.astype(np.float32), axis=1))
    energy = gx + gy
    thr = float(np.median(energy) + np.std(energy))
    mask = (energy > thr).astype(np.uint8)
    # 形态学闭合，得到主体连通块
    kernel = np.ones((9, 9), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return 0.0, 0.0, 0.0
    # 取最大非背景块
    sizes = stats[1:, cv2.CC_STAT_AREA]
    big = int(np.argmax(sizes)) + 1
    bx, by, bw, bh, ba = stats[big][:5]
    subject_ratio = float(ba) / float(cw * ch)
    # 质心相对画面中心偏移
    cx = bx + bw / 2.0
    cy = by + bh / 2.0
    off_x = abs(cx - w / 2.0) / (w / 2.0)
    off_y = abs(cy - h / 2.0) / (h / 2.0)
    return subject_ratio, off_x, off_y


def check_frame(img_bgr: np.ndarray, subtitle_zone: float = 0.22,
                corner_ratio: float = 0.12) -> dict:
    """单帧画面干净检查。

    Args:
        img_bgr: BGR 图像（cv2.imread 默认格式）
        subtitle_zone: 底部字幕区占画面高度比例
        corner_ratio: 四角异物检测区域占画面宽高比例

    Returns:
        dict: {clean_score, level, dims{...}, issues[], advice[]}
    """
    if img_bgr is None:
        return {"clean_score": 0.0, "level": "无效", "dims": {},
                "issues": ["图像为空"], "advice": ["无法检测"]}
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # ---- 1. 背景杂乱度（排除字幕区）----
    sub_top = int(h * (1 - subtitle_zone))
    body = gray[:sub_top, :] if sub_top > 0 else gray
    edge_ratio = _edge_density(body)
    texture = _laplacian_sharpness(body)
    corners = _corner_density(body)
    # 边缘密度 > 0.16 视为杂乱；纹理方差按经验尺度归一
    clutter_pen = 0.0
    if edge_ratio > 0.16:
        clutter_pen = min(3.0, (edge_ratio - 0.16) * 25)
    if corners > 8.0:
        clutter_pen += min(2.0, (corners - 8.0) * 0.3)
    clutter_score = _clamp(10.0 - clutter_pen)

    # ---- 2. 叠影重影（清晰度 + 多重边缘）+ 噪点惩罚 ----
    sharp = _laplacian_sharpness(gray)
    # 经验尺度：口播室内画面清晰时 lap.var 一般 > 200；< 60 明显模糊/重影
    sharp_score = _clamp(10.0 * min(1.0, max(0.0, (sharp - 60.0) / 200.0)))
    # 噪点率：原图与中值滤波残差均值。正常画面 < 4，噪点帧 > 8。
    # 防止"噪点多→拉普拉斯方差高→误判清晰"的伪高分。
    med = cv2.medianBlur(gray, 5)
    noise_ratio = float(np.abs(gray.astype(np.float32) - med.astype(np.float32)).mean())
    noise_pen = min(4.0, max(0.0, (noise_ratio - 6.0) * 0.8))
    ghost_score = _clamp(sharp_score - noise_pen)

    # ---- 3. 主体占比与构图 ----
    subj_ratio, off_x, off_y = _subject_region(gray, edge_ratio)
    ratio_score = _clamp(10.0 * min(1.0, subj_ratio / 0.22))   # 主体占比约 22% 为基准
    center_pen = (off_x * 0.5 + off_y * 0.5) * 4.0
    subject_score = _clamp(ratio_score - center_pen)

    # ---- 4. 字幕遮挡 ----
    sub_zone = gray[int(h * (1 - subtitle_zone)):, :]
    sub_edge = _edge_density(sub_zone, low=80, high=200)
    subtitle_present = sub_edge > 0.08
    # 字幕与主体中心列重叠检查
    overlap = False
    if subtitle_present:
        cx = w // 2
        col_left, col_right = int(w * 0.3), int(w * 0.7)
        sub_col = gray[int(h * (1 - subtitle_zone)):, col_left:col_right]
        if _edge_density(sub_col, low=80, high=200) > 0.10:
            overlap = True
    subtitle_score = _clamp(8.0 if (subtitle_present and not overlap)
                            else (4.0 if overlap else 9.5))

    # ---- 5. 角落异物（水印/图标/界面元素）----
    cw, chh = int(w * corner_ratio), int(h * corner_ratio)
    corners_like = 0.0
    for (x0, y0) in [(0, 0), (w - cw, 0), (0, h - chh), (w - cw, h - chh)]:
        zone = gray[y0:y0 + chh, x0:x0 + cw]
        if zone.size == 0:
            continue
        ed = _edge_density(zone, low=80, high=200)
        sat = cv2.cvtColor(img_bgr[y0:y0 + chh, x0:x0 + cw],
                           cv2.COLOR_BGR2HSV)[:, :, 1].mean() / 255.0
        if ed > 0.12 and sat > 0.25:
            corners_like += 1.0
    corner_score = _clamp(10.0 - corners_like * 2.5)

    # ---- 总分与问题 ----
    dims = {
        "clutter": round(clutter_score, 2),      # 背景杂乱度
        "ghost": round(ghost_score, 2),          # 叠影重影/清晰度
        "subject": round(subject_score, 2),      # 主体构图
        "subtitle": round(subtitle_score, 2),    # 字幕遮挡
        "corner": round(corner_score, 2),        # 角落异物
    }
    weights = {"clutter": 0.30, "ghost": 0.25, "subject": 0.20,
               "subtitle": 0.15, "corner": 0.10}
    clean_score = round(sum(dims[k] * weights[k] for k in weights) * 10.0, 1)

    issues, advice = [], []
    if clutter_score < 7:
        issues.append("背景杂乱（装饰物/纹理过多，分散注意力）")
        advice.append("换纯色/浅色背景，或对背景加轻度模糊（v3.1 蒙板/滤镜可做）")
    if ghost_score < 7:
        issues.append("画面有叠影/重影或清晰度不足（双重曝光或运动模糊）")
        advice.append("检查素材是否为慢门/叠影拍摄；重拍或用锐化+去模糊；避免导出帧残影")
    if subject_score < 6:
        issues.append("主体占比小或偏离中心，构图松散")
        advice.append("放大主体（scale 推近）、居中构图；参考 shotprof 景别建议")
    if subtitle_score < 6:
        issues.append("字幕遮挡主体（底部字幕与主体中心列重叠）")
        advice.append("字幕上移/缩小，或主体构图避开底部 20% 区域")
    if corner_score < 7:
        issues.append("角落存在疑似水印/图标/界面元素")
        advice.append("移除水印；导出前关闭播放器控件/剪映界面叠加")
    if not issues:
        advice.append("画面干净，无需处理")

    level = next((lv for thr, lv, _ in CLEAN_LEVELS if clean_score >= thr), CLEAN_LEVELS[-1][1])
    return {
        "clean_score": clean_score,
        "level": level,
        "dims": dims,
        "meta": {"subtitle_present": subtitle_present,
                 "corner_like_count": int(corners_like),
                 "sharpness": round(sharp, 1),
                 "noise_ratio": round(noise_ratio, 2),
                 "edge_density": round(edge_ratio, 3),
                 "subject_ratio": round(subj_ratio, 3)},
        "issues": issues,
        "advice": advice,
    }


def check_image_file(path: str) -> dict:
    """按文件路径检查单帧画面。"""
    img = cv2.imread(path)
    result = check_frame(img)
    result["source"] = path
    return result
