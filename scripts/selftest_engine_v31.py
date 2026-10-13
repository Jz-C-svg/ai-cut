#!/usr/bin/env python3
r"""v3.1 引擎层四件套自测：复合片段 Group + 绿幕 chroma-key + 蒙板六类 + 轨道层级 11 层。

字段级断言（不是"看起来对"）：
  Group combine   成员片段 group_id 相同且非空；bounding range = [min start, max end]
  绿幕 chroma     materials.chromas 非空、type=chromas、color/intensity/shadow 正确；
                  片段 extra_material_refs 引用了该素材；绿/蓝/白三幕各断言一次
  蒙板六类        materials.masks 含六类（线性/镜面/圆形/矩形/爱心/星形）；
                  每条 config.feather / config.invert / resource_id 命中
  轨道层级        validate_track_order 返回 ok(bool) 且 ranks 非空；track_rank 单调
  持久化          save + reload 后 chroma / mask / group_id 仍在

全部在 work/selftest/engine_v31 里做副本，不碰真实草稿。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, edit2  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="v3.1 引擎层四件套自测")
    ap.add_argument("--draft", default="月子知识-成片")
    args = ap.parse_args()

    src = cli.resolve_draft(args.draft)
    case = ROOT / "work" / "selftest" / "engine_v31"
    shutil.rmtree(case, ignore_errors=True)
    case.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, case)

    d = Draft.load(case)
    print(f"样本：{src} · {d.declared_duration:.3f}s · 视频 {len(d.video_segments)} · 文本 {len(d.text_segments)}")
    vids = d.video_segments
    if len(vids) < 6:
        print("!! 视频片段不足 6 个，蒙板六类将复用片段")

    # ============ 1. 绿幕 chroma-key：绿/蓝/白三幕 ============
    chroma_reps = []
    for i, (preset, expect_hex) in enumerate([("绿幕", "#00FF00"), ("蓝幕", "#0000FF"), ("白幕", "#FFFFFF")]):
        seg = vids[i % len(vids)]
        rep = edit2.add_chroma_key(d, seg.id, preset, strength=0.7, shadow=0.1)
        chroma_reps.append((preset, expect_hex, rep, seg))

    chroma_mats = d.materials_of("chromas")
    check("chroma：materials.chromas 非空且 type=chromas",
          len(chroma_mats) == 3 and all(m.get("type") == "chromas" for m in chroma_mats),
          f"chromas={len(chroma_mats)} 条")
    # 三幕颜色逐一断言
    color_ok = True
    detail = []
    for preset, expect_hex, rep, seg in chroma_reps:
        mat = d.material(rep["material"])
        ok = mat.get("color") == expect_hex and abs(mat.get("intensity", 0) - 0.7) < 1e-6 \
            and mat.get("shadow") == 0.1 and mat.get("path") == ""
        detail.append(f"{preset}={mat.get('color')}")
        if not ok:
            color_ok = False
    check("chroma：绿/蓝/白幕 color 与 intensity/shadow/path 正确", color_ok, " · ".join(detail))
    # extra_material_refs 引用
    ref_ok = all(rep["material"] in (seg.raw.get("extra_material_refs") or [])
                 for _, _, rep, seg in chroma_reps)
    check("chroma：片段 extra_material_refs 引用了 chroma 素材", ref_ok,
          f"refs={chroma_reps[0][2]['material'][:8]}...")

    # ============ 2. 蒙板六类 ============
    mask_types = ["线性", "镜面", "圆形", "矩形", "爱心", "星形"]
    mask_reps = {}
    for i, mt in enumerate(mask_types):
        seg = vids[i % len(vids)]
        rc = 20.0 if mt == "矩形" else 0.0
        rep = edit2.add_mask(d, seg.id, mt, feather=30.0, invert=(i % 2 == 1),
                             rotation=15.0, round_corner=rc)
        mask_reps[mt] = rep

    masks_mat = d.materials_of("masks")
    names = {m.get("name") for m in masks_mat}
    check("蒙板：materials.masks 含六类（线性/镜面/圆形/矩形/爱心/星形）",
          all(n in names for n in mask_types),
          f"names={sorted(names)}")
    # 逐类断言 feather/invert 字段命中
    field_ok = True
    fdetail = []
    for mt in mask_types:
        mat = d.material(mask_reps[mt]["material"])
        cfg = mat.get("config", {})
        expect_inv = (mask_types.index(mt) % 2 == 1)
        ok = (abs(cfg.get("feather", -1) - 0.3) < 1e-6
              and cfg.get("invert") is expect_inv
              and mat.get("type") == "mask")
        fdetail.append(f"{mt}:f={cfg.get('feather')},inv={cfg.get('invert')}")
        if not ok:
            field_ok = False
    check("蒙板：六类 config.feather=0.3 / config.invert 交替 / type=mask 命中",
          field_ok, " · ".join(fdetail))
    # 矩形圆角单独断言
    rect_mat = d.material(mask_reps["矩形"]["material"])
    check("蒙板：矩形 config.roundCorner=0.2（圆角 20）",
          abs(rect_mat.get("config", {}).get("roundCorner", -1) - 0.2) < 1e-6,
          f"roundCorner={rect_mat.get('config', {}).get('roundCorner')}")
    # extra_material_refs
    mref_ok = all(d.material(r["material"]) is not None and
                  r["material"] in (next(s for s in d.all_segments() if s.id == r["segment"]).raw.get("extra_material_refs") or [])
                  for r in mask_reps.values())
    check("蒙板：片段 extra_material_refs 引用了 mask 素材", mref_ok, "")

    # ============ 3. 复合片段 Group combine ============
    g0, g1 = vids[0], vids[min(1, len(vids) - 1)]
    rep_g = edit2.combine_segments(d, [g0.id, g1.id])
    gid = rep_g["group_id"]
    check("Group：两成员片段 group_id 相同且非空",
          bool(gid) and g0.raw.get("group_id") == gid and g1.raw.get("group_id") == gid,
          f"group_id={gid[:8]}...")
    lo = min(g0.t, g1.t)
    hi = max(g0.end, g1.end)
    check("Group：bounding range = [min start, max end]",
          abs(rep_g["range"][0] - round(lo, 3)) < 0.01 and abs(rep_g["range"][1] - round(hi, 3)) < 0.01,
          f"range={rep_g['range']} · 期望≈[{round(lo, 2)},{round(hi, 2)}]")

    # ============ 4. 轨道层级 11 层校验 ============
    vr = edit2.validate_track_order(d)
    check("轨道层级：validate_track_order 返回 ok(bool) 且 ranks 非空",
          isinstance(vr["ok"], bool) and len(vr["ranks"]) > 0,
          f"ok={vr['ok']} · ranks={len(vr['ranks'])} · violations={len(vr['violations'])}")
    from JianYingDraft.track_ranking import track_rank
    check("轨道层级：track_rank 单调（audio<video<effect<sticker<text）",
          track_rank("audio") < track_rank("video") < track_rank("effect") < track_rank("sticker") < track_rank("text"),
          f"a{vids and ''} ranks={track_rank('audio')},{track_rank('video')},{track_rank('effect')},{track_rank('sticker')},{track_rank('text')}")

    # ============ 5. 持久化 save + reload ============
    d.save()
    rl = Draft.load(case)
    rl_chroma = rl.material(chroma_reps[0][2]["material"])
    rl_mask = rl.material(mask_reps["圆形"]["material"])
    rl_g0 = next(s for s in rl.all_segments() if s.id == g0.id)
    check("持久化：chroma color/intensity 仍在",
          rl_chroma.get("color") == "#00FF00" and abs(rl_chroma.get("intensity", 0) - 0.7) < 1e-6,
          f"color={rl_chroma.get('color')}")
    check("持久化：mask config.feather 与 group_id 仍在",
          abs(rl_mask.get("config", {}).get("feather", -1) - 0.3) < 1e-6
          and rl_g0.raw.get("group_id") == gid,
          f"feather={rl_mask.get('config', {}).get('feather')} · group_id={rl_g0.raw.get('group_id', '')[:8]}...")

    shutil.rmtree(case, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
