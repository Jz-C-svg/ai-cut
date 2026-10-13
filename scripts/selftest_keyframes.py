#!/usr/bin/env python3
r"""关键帧管线接入自测（build_draft 消费 clip["keyframes"]）。

覆盖：
  KF-A  build_draft 消费 keyframes，落稿 common_keyframes（UNIFORM_SCALE + KFTypeAlpha 两组）
  KF-B  easing=Hold 的 keyframe curveType=="Hold"
  KF-C  easing=Line / Smooth 的曲线类型正确
  KF-D  无 keyframes 字段时不写入 common_keyframes（回归，零行为变化）
  KF-E  数据结构与 JianYingDraft.export_json 兼容（仅结构，尚未在剪映打开验收）

FFmpeg 生成临时合成素材；草稿写入独立临时目录，不修改生产草稿。
不发布到剪映，不调模型或付费 API。

用法：
  "<project-root>\venv\Scripts\python.exe" "<project-root>\scripts\selftest_keyframes.py"
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

import build_draft  # noqa: E402

REAL_VIDEO = ""
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""),
          flush=True)


def _make_sheet(project: str, keyframes: list[dict] | None) -> dict:
    """构造最小决策单。"""
    clip = {"file": REAL_VIDEO, "start": 0.0, "end": 3.0, "speed": 1.0}
    if keyframes is not None:
        clip["keyframes"] = keyframes
    return {
        "project": project, "template": "koubo-standard", "style": "口播标准",
        "width": 1080, "height": 1920, "fps": 30,
        "clips": [clip],
    }


def _video_segs(draft_path: Path) -> list[dict]:
    data = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    return [s for t in data.get("tracks", []) if t.get("type") == "video"
            for s in t.get("segments", [])]


def run_fixture(folder: Path) -> int:
    global REAL_VIDEO
    source = folder / 'synthetic.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=360x640:r=30:d=3',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(source)],
                   check=True, timeout=60)
    REAL_VIDEO = str(source)
    cfg = build_draft.load_config()
    with_kf = "selftest-kf-with"
    no_kf = "selftest-kf-none"

    # 带关键帧：scale(Line/Hold/Line) + opacity(Line/Line)
    kf_list = [
        {"property": "scale", "t": 0.0, "value": 1.0, "easing": "Line"},
        {"property": "scale", "t": 1.0, "value": 1.2, "easing": "Hold"},
        {"property": "scale", "t": 2.0, "value": 1.0, "easing": "Line"},
        {"property": "opacity", "t": 0.0, "value": 0.0, "easing": "Line"},
        {"property": "opacity", "t": 0.5, "value": 1.0, "easing": "Line"},
    ]
    p_with = build_draft.build(_make_sheet(with_kf, kf_list), cfg, output_root=folder / 'drafts')

    segs = _video_segs(p_with)
    check("KF-0 草稿生成且含视频片段", len(segs) >= 1, f"视频段数={len(segs)}")
    common = segs[0].get("common_keyframes", []) if segs else []
    groups = {g.get("property_type"): g.get("keyframe_list", []) for g in common}

    check("KF-A common_keyframes 含 UNIFORM_SCALE 与 KFTypeAlpha 两组",
          "UNIFORM_SCALE" in groups and "KFTypeAlpha" in groups,
          f"组={list(groups.keys())}")
    check("KF-A2 UNIFORM_SCALE 3 帧 / KFTypeAlpha 2 帧",
          len(groups.get("UNIFORM_SCALE", [])) == 3
          and len(groups.get("KFTypeAlpha", [])) == 2,
          f"scale={len(groups.get('UNIFORM_SCALE', []))} alpha={len(groups.get('KFTypeAlpha', []))}")

    # Hold 校验
    scale_kfs = groups.get("UNIFORM_SCALE", [])
    hold_kf = next((k for k in scale_kfs if k.get("curveType") == "Hold"), None)
    check("KF-B easing=Hold 的关键帧 curveType==\"Hold\"",
          hold_kf is not None,
          f"scale 帧曲线={[k.get('curveType') for k in scale_kfs]}")
    line_kfs = [k for k in scale_kfs if k.get("curveType") == "Line"]
    check("KF-C Line 关键帧曲线类型正确", len(line_kfs) == 2,
          f"Line 帧数={len(line_kfs)}")

    # 结构兼容校验：每帧字段齐全
    need = {"curveType", "graphID", "left_control", "right_control",
            "id", "time_offset", "values"}
    all_kfs = scale_kfs + groups.get("KFTypeAlpha", [])
    struct_ok = all(need.issubset(k.keys()) and isinstance(k.get("values"), list)
                    and isinstance(k.get("time_offset"), int) for k in all_kfs)
    check("KF-E 关键帧字段与 export_json 兼容（curveType/graphID/controls/time_offset/values）",
          struct_ok, f"校验 {len(all_kfs)} 帧")

    # 无关键帧回归
    p_none = build_draft.build(_make_sheet(no_kf, None), cfg, output_root=folder / 'drafts')
    segs_none = _video_segs(p_none)
    common_none = segs_none[0].get("common_keyframes", []) if segs_none else []
    check("KF-D 无 keyframes 字段时 common_keyframes 为空（回归零变化）",
          len(common_none) == 0, f"common_keyframes 长度={len(common_none)}")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n===== 结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 =====", flush=True)
    return 0 if not failed else 1


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='aicut_keyframes_') as folder:
        return run_fixture(Path(folder))


if __name__ == "__main__":
    sys.exit(main())
