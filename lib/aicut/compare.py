# -*- coding: utf-8 -*-
"""画面 before/after 比对（compare）：原素材帧 vs 成片帧

- 并排对比图（统一尺寸，标注 before / after）
- 差异热区标注（显著差异连通域画框，Top N）
- 指标对比（亮度 / 饱和度 / 清晰度 / 边缘密度 / 干净度分）
- 输出：对比 PNG + JSON 指标，供导出报告与评审链路使用
"""
from __future__ import annotations

import os

import numpy as np

try:
    import cv2
except ImportError as _e:  # pragma: no cover
    raise ImportError("画面比对需要 opencv-python-headless") from _e

from .cleancheck import check_frame

__all__ = ["compare_frames", "compare_files", "compare_folder_samples"]


def _resize_to(img, width=640):
    h, w = img.shape[:2]
    scale = width / w
    return cv2.resize(img, (width, int(h * scale)), interpolation=cv2.INTER_AREA)


def _metrics(img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    clean = check_frame(img_bgr)
    return {
        "brightness": round(float(gray.mean()), 1),
        "saturation": round(float(hsv[:, :, 1].mean()), 1),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        "edge_density": round(float(cv2.Canny(gray, 60, 160).mean() / 255.0), 3),
        "clean_score": clean["clean_score"],
        "clean_level": clean["level"],
        "dims": clean["dims"],
        "issues": clean["issues"],
    }


def _diff_boxes(before, after, top_n=4, box_color=(245, 34, 45), thickness=3):
    """显著差异连通域 → 画框。返回 (annotated_after, boxes)。"""
    b = cv2.cvtColor(cv2.resize(before, (after.shape[1], after.shape[0])),
                     cv2.COLOR_BGR2GRAY).astype(np.float32)
    a = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY).astype(np.float32)
    diff = np.abs(a - b)
    diff = cv2.GaussianBlur(diff, (5, 5), 0)
    _, mask = cv2.threshold(diff, max(18.0, float(diff.mean() + diff.std())), 255,
                            cv2.THRESH_BINARY)
    mask = mask.astype(np.uint8)
    kernel = np.ones((11, 11), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    areas = [(i, int(stats[i, cv2.CC_STAT_AREA])) for i in range(1, n)]
    areas.sort(key=lambda x: -x[1])
    annotated = after.copy()
    boxes = []
    for i, area in areas[:top_n]:
        if area < after.shape[0] * after.shape[1] * 0.002:
            continue
        x, y, w, h = stats[i][:4]
        boxes.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                      "area": int(area)})
        cv2.rectangle(annotated, (x, y), (x + w, y + h), box_color, thickness)
        cv2.putText(annotated, f"DIFF {area}", (x, max(18, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2)
    return annotated, boxes


def compare_frames(before_bgr, after_bgr, out_path=None, label_before="原素材 Before",
                   label_after="成片 After") -> dict:
    """并排比对两帧，生成对比图与指标。"""
    b = _resize_to(before_bgr, 640)
    a = _resize_to(after_bgr, 640)
    annotated, boxes = _diff_boxes(b, a)

    h1 = max(b.shape[0], a.shape[0], annotated.shape[0])
    def _pad(img):
        p = np.full((h1, img.shape[1], 3), 245, np.uint8)
        p[:img.shape[0], :, :] = img
        return p
    b_p, a_p = _pad(b), _pad(annotated)

    label_h = 34
    canvas = np.full((h1 + label_h, b_p.shape[1] + a_p.shape[1], 3), 250, np.uint8)
    canvas[label_h:, :b_p.shape[1]] = b_p
    canvas[label_h:, b_p.shape[1]:] = a_p
    cv2.putText(canvas, label_before, (12, 22), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (24, 88, 217), 2)
    cv2.putText(canvas, label_after, (b_p.shape[1] + 12, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (245, 34, 45), 2)
    # 中缝分隔线
    cv2.line(canvas, (b_p.shape[1], label_h), (b_p.shape[1], h1 + label_h),
             (180, 180, 180), 2)

    result = {
        "before_metrics": _metrics(before_bgr),
        "after_metrics": _metrics(after_bgr),
        "diff_boxes": boxes,
        "diff_count": len(boxes),
    }
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        cv2.imwrite(out_path, canvas)
        result["output"] = out_path
    return result


def compare_files(before_path, after_path, out_path=None) -> dict:
    """按文件路径比对两张图。"""
    b = cv2.imread(before_path)
    a = cv2.imread(after_path)
    if b is None or a is None:
        raise ValueError(f"图像读取失败: {before_path} / {after_path}")
    result = compare_frames(b, a, out_path=out_path)
    result["before_source"] = before_path
    result["after_source"] = after_path
    return result


def compare_folder_samples(video_path, after_path, out_dir, frame_sec=1.0) -> dict:
    """从素材视频抽一帧作为 before，与成片帧比对（模拟测试用）。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"视频无法打开: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_MSEC, frame_sec * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise ValueError(f"抽帧失败: {video_path} @ {frame_sec}s")
    after = cv2.imread(after_path)
    if after is None:
        raise ValueError(f"成片帧无法读取: {after_path}")
    name = os.path.splitext(os.path.basename(video_path))[0]
    out = os.path.join(out_dir, f"compare_{name}.png")
    return compare_frames(frame, after, out_path=out,
                          label_before=f"素材帧 {name}@ {frame_sec}s",
                          label_after="成片 After")
