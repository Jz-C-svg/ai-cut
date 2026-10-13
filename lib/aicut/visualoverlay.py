"""视觉智能适配引擎 · 智能叠加引擎（v3.7.0）。

读取 build_draft 已生成的剪映草稿 draft_content.json，把 visualmap 产出的"视觉计划"
直接写进草稿：PNG 贴纸走 materials.videos（type=photo）+ "visual-overlay" 视频轨道；
纯文字花字走 materials.texts + "visual-text" 文本轨道。

不依赖 JianYingDraft 库的 save（草稿已由 build_draft 落盘），只做 JSON 增量写。
所有时间单位为微秒（int）。画布 1080x1920，transform 单位 = 半个画布宽/高。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LIBRARY = ROOT / "content-library" / "visuals"

# 位置 -> (transform_x, transform_y, 图片缩放)；单位：半个画布宽/高
_POS_TABLE: dict[str, tuple[float, float, float]] = {
    "top_left": (-0.5, -0.55, 0.35),
    "top_center": (0.0, -0.55, 0.40),
    "top_right": (0.5, -0.55, 0.35),
    "center": (0.0, -0.10, 0.50),
    "bottom_left": (-0.5, 0.35, 0.30),
    "bottom_right": (0.5, 0.35, 0.30),
}


def position_to_clip(position: str, *, as_text: bool = False) -> dict:
    """位置 -> clip_settings 各量。文字段字号控制大小，缩放恒为 1.0。"""
    tx, ty, scale = _POS_TABLE.get(position, (0.0, -0.55, 0.40))
    s = 1.0 if as_text else scale
    return {
        "transform_x": tx,
        "transform_y": ty,
        "scale_x": s,
        "scale_y": s,
        "alpha": 0.95,
    }


def _hex_to_rgb01(hex_color: str) -> list[float]:
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return [round(int(h[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)]


def _new_id() -> str:
    return uuid.uuid4().hex


def _make_speed_material() -> tuple[str, dict]:
    sid = _new_id()
    return sid, {"curve_speed": None, "id": sid, "mode": 0,
                 "speed": 1.0, "type": "speed"}


def _clip_json(clip: dict) -> dict:
    """position_to_clip 的输出 -> 草稿 clip 字段（嵌套结构，与库导出一致）。"""
    return {
        "alpha": clip["alpha"],
        "flip": {"horizontal": False, "vertical": False},
        "rotation": 0.0,
        "scale": {"x": clip["scale_x"], "y": clip["scale_y"]},
        "transform": {"x": clip["transform_x"], "y": clip["transform_y"]},
    }


def _base_segment(material_id: str, t0_us: int, dur_us: int) -> dict:
    return {
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "last_nonzero_volume": 1.0,
        "reverse": False,
        "track_attribute": 0,
        "track_render_index": 0,
        "visible": True,
        "id": _new_id(),
        "material_id": material_id,
        "target_timerange": {"start": int(t0_us), "duration": int(dur_us)},
        "common_keyframes": [],
        "keyframe_refs": [],
    }


def _make_image_material(asset_path: Path) -> dict:
    """生成 PNG 图片素材 dict（type=photo）。"""
    # 读真实宽高（失败回退 512）
    w = h = 512
    try:
        from PIL import Image as _PILImage  # 延迟导入
        with _PILImage.open(asset_path) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001
        pass
    mid = _new_id()
    return {
        "audio_fade": None,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": {"upper_left_x": 0.0, "upper_left_y": 0.0,
                 "upper_right_x": 1.0, "upper_right_y": 0.0,
                 "lower_left_x": 0.0, "lower_left_y": 1.0,
                 "lower_right_x": 1.0, "lower_right_y": 1.0},
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": 10800000000,
        "height": int(h),
        "id": mid,
        "local_material_id": "",
        "material_id": mid,
        "material_name": asset_path.name,
        "media_path": "",
        "path": str(asset_path),
        "type": "photo",
        "width": int(w),
    }


def _make_image_segment(mat: dict, t0_us: int, dur_us: int, clip: dict) -> dict:
    sid, speed_mat = _make_speed_material()
    seg = _base_segment(mat["id"], t0_us, dur_us)
    seg.update({
        "source_timerange": {"start": 0, "duration": int(dur_us)},
        "speed": 1.0,
        "volume": 0.0,
        "extra_material_refs": [sid],
        "is_tone_modify": False,
        "clip": _clip_json(clip),
        "uniform_scale": {"on": True, "value": 1.0},
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "render_index": 0,
    })
    seg["_speed_material"] = speed_mat  # 内部附带，写入后剔除
    return seg


def _make_text_material(content: str, style: dict) -> tuple[str, dict]:
    """生成文本素材 dict（content 为 JSON 字符串）。"""
    mid = _new_id()
    rgb = _hex_to_rgb01(str(style.get("color", "#FFFFFF")))
    size = float(style.get("font_size", 7.0))
    bold = bool(style.get("bold", False))
    fill = {"alpha": 1.0, "content": {"render_type": "solid",
            "solid": {"alpha": 1.0, "color": rgb}}}
    style0 = {
        "fill": fill,
        "range": [0, len(content)],
        "size": size,
        "bold": bold,
        "italic": False,
        "underline": False,
        "strokes": [{"content": {"solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}},
                     "width": 0.04}],
    }
    payload = {"styles": [style0], "text": content}
    mat = {
        "id": mid,
        "content": json.dumps(payload, ensure_ascii=False),
        "typesetting": 0,
        "alignment": 1,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": 7,
        "type": "text",
        "global_alpha": 1.0,
    }
    bg = style.get("bg_color")
    if bg:
        mat["check_flag"] = 7 | 16
        mat["background_style"] = 1
        mat["background_color"] = str(bg)
        mat["background_alpha"] = 0.85
        mat["background_round_radius"] = 0.5
        mat["background_height"] = 0.18
        mat["background_width"] = 0.18
        mat["background_horizontal_offset"] = 0.0
        mat["background_vertical_offset"] = 0.0
    return mid, mat


def _make_text_segment(mat: dict, t0_us: int, dur_us: int, clip: dict) -> dict:
    sid, speed_mat = _make_speed_material()
    seg = _base_segment(mat["id"], t0_us, dur_us)
    seg.update({
        "source_timerange": None,
        "speed": 1.0,
        "volume": 1.0,
        "extra_material_refs": [sid],
        "is_tone_modify": False,
        "clip": _clip_json(clip),
        "uniform_scale": {"on": True, "value": 1.0},
        "render_index": 15000,
    })
    seg["_speed_material"] = speed_mat
    return seg


def _ensure_track(tracks: list[dict], track_type: str, name: str) -> dict:
    for t in tracks:
        if t.get("type") == track_type and t.get("name") == name:
            return t
    track = {"attribute": 0, "flag": 0, "id": _new_id(),
             "is_default_name": False, "name": name, "segments": [], "type": track_type}
    tracks.append(track)  # 加到末尾，保证叠加在视频/字幕之上
    return track


def apply_to_draft(draft_dir: str | Path, visual_plan: list[dict],
                   library_dir: str | Path | None = None) -> dict:
    """把视觉计划写进草稿，返回统计。"""
    draft_dir = Path(draft_dir)
    content_path = draft_dir / "draft_content.json"
    stats = {"added": 0, "by_type": {}, "skipped": 0}
    if not visual_plan:
        return stats
    if not content_path.exists():
        raise FileNotFoundError(f"草稿不存在: {content_path}")

    data = json.loads(content_path.read_text(encoding="utf-8-sig"))
    materials = data.setdefault("materials", {})
    videos = materials.setdefault("videos", [])
    texts = materials.setdefault("texts", [])
    speeds = materials.setdefault("speeds", [])
    tracks = data.setdefault("tracks", [])

    lib = Path(library_dir) if library_dir else DEFAULT_LIBRARY
    if not lib.is_absolute():
        lib = ROOT / lib

    img_track = _ensure_track(tracks, "video", "visual-overlay")
    txt_track = _ensure_track(tracks, "text", "visual-text")

    for item in visual_plan:
        t0 = max(0.0, float(item.get("t0", 0.0)))
        t1 = float(item.get("t1", t0))
        dur = t1 - t0
        if dur < 0.1:
            stats["skipped"] += 1
            continue
        t0_us = int(round(t0 * 1_000_000))
        dur_us = int(round(dur * 1_000_000))
        vtype = item.get("type", "")
        asset = item.get("asset")
        try:
            if asset:
                asset_path = lib / asset
                if not asset_path.exists():
                    print(f"[视觉叠加] 跳过缺失素材: {asset}")
                    stats["skipped"] += 1
                    continue
                mat = _make_image_material(asset_path)
                videos.append(mat)
                clip = position_to_clip(item.get("position", "top_right"), as_text=False)
                seg = _make_image_segment(mat, t0_us, dur_us, clip)
                sp = seg.pop("_speed_material")
                speeds.append(sp)
                img_track["segments"].append(seg)
            else:
                content = str(item.get("content", "")).strip()
                if not content:
                    stats["skipped"] += 1
                    continue
                mid, mat = _make_text_material(content, item.get("style", {}))
                texts.append(mat)
                clip = position_to_clip(item.get("position", "top_center"), as_text=True)
                seg = _make_text_segment(mat, t0_us, dur_us, clip)
                sp = seg.pop("_speed_material")
                speeds.append(sp)
                txt_track["segments"].append(seg)
        except Exception as exc:  # noqa: BLE001
            print(f"[视觉叠加] 单项失败({type(exc).__name__}: {exc})，跳过")
            stats["skipped"] += 1
            continue
        stats["added"] += 1
        stats["by_type"][vtype] = stats["by_type"].get(vtype, 0) + 1

    content_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return stats


if __name__ == "__main__":
    # 简单自测：对一个草稿目录空跑
    import sys
    if len(sys.argv) > 1:
        r = apply_to_draft(sys.argv[1], [])
        print(r)
    else:
        print("position_to_clip:", position_to_clip("top_right"))
        print("as_text:", position_to_clip("top_center", as_text=True))
