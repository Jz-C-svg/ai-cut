#!/usr/bin/env python3
r"""增量编辑原语自测：切分 / 加字幕 / 加转场 / 加滤镜 / 删片段 / 调音量。

每个原语都按"不变量"验收，而不是"看起来对"：
  切分   左右两段长度之和不变；右段源入点 = 左段源入点 + 左段源长；无重叠
  字幕   文本素材与片段成对新增；样式沿用模板；时长精确；渲染层级正确
  转场   素材进 materials.transitions 且被片段引用；名称与时长正确
  滤镜   素材进 materials.filters 且被引用；强度转成 0-1
  删除   时长减少等于片段长度；无重叠；草稿仍可读

全部在 work/selftest 里做副本，不碰真实草稿。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, edit, edit2, verify  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="增量编辑原语自测")
    ap.add_argument("--draft", default="月子知识-成片")
    args = ap.parse_args()

    src = cli.resolve_draft(args.draft)
    case = ROOT / "work" / "selftest" / "edit2"
    shutil.rmtree(case, ignore_errors=True)
    case.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, case)
    base = json.loads((src / "draft_content.json").read_text(encoding="utf-8"))

    # ---- 1. 切分 ----
    d = Draft.load(case)
    before = edit2.split_at(d, 3.9)
    check("切分：至少切开一段", len(before["cut"]) >= 1,
          "、".join(f"{c['track']}#{c['segment'][:6]}" for c in before["cut"]))
    vids = [s for s in d.video_segments]
    check("切分：视频段数增加", len(vids) == 3, f"{len(vids)} 段")
    a, b = vids[0], vids[1]
    check("切分：左右长度之和 = 原长", abs((a.length + b.length) - 3.912) < 0.01,
          f"{a.length:.3f} + {b.length:.3f} = {a.length + b.length:.3f}")
    check("切分：源时间连续", a.src_in is not None and b.src_in is not None
          and abs((a.src_in + a.src_len) - b.src_in) < 0.01,
          f"左源 {a.src_in:.2f}+{a.src_len:.2f} vs 右源 {b.src_in:.2f}")
    check("切分：切点落在 3.9s", abs(b.t - 3.9) < 0.01, f"{b.t:.3f}s")
    check("切分：无重叠", verify.check_no_overlap(d).ok)
    d.save()
    reloaded = Draft.load(case)
    check("切分：落盘后可重新读取", len(reloaded.video_segments) == 3,
          f"时长 {reloaded.declared_duration:.3f}s")

    # ---- 2. 加字幕 ----
    d = Draft.load(case)
    n_before = len(d.text_segments)
    rep = edit2.add_subtitle(d, "这是一条测试字幕", 1.0, 2.5)
    check("字幕：片段数 +1", len(d.text_segments) == n_before + 1, f"{n_before} -> {len(d.text_segments)}")
    new_mat = d.material(rep["material"])
    check("字幕：素材已建立且含样式", bool(new_mat) and "styles" in (new_mat.get("content") or ""),
          f"复用模板={rep['用模板样式']}")
    content = json.loads(new_mat["content"])
    check("字幕：文本与样式范围对齐", content.get("text") == "这是一条测试字幕"
          and content["styles"][0]["range"] == [0, len("这是一条测试字幕")],
          str(content["styles"][0]["range"]))
    seg = next(s for s in d.text_segments if s.id == rep["segment"])
    check("字幕：时长精确 1.5s", abs(seg.length - 1.5) < 0.005, f"{seg.length:.3f}s")
    check("字幕：层级 15000（在视频之上）", seg.raw.get("render_index") == 15000)
    check("字幕：与既有字幕重叠时自动落到新轨", bool(rep["重叠"]) and rep["落到新轨"]
          and verify.check_no_overlap(d, "text").ok,
          f"重叠 {len(rep['重叠'])} 条，另起一轨={rep['落到新轨']}")

    # ---- 3. 转场 ----
    n_tr = len(d.materials_of("transitions"))
    tr = edit2.add_transition(d, d.video_segments[0].id, "模糊", seconds=0.6)
    check("转场：素材 +1 且被引用", len(d.materials_of("transitions")) == n_tr + 1
          and tr["material"] in (d.video_segments[0].raw.get("extra_material_refs") or []),
          f"{tr['名称']} {tr['时长秒']}s")
    mat = d.material(tr["material"])
    check("转场：时长与名称正确", mat["duration"] == 600000 and mat["type"] == "transition",
          f"{mat['duration']}us / {mat['name']}")

    # ---- 4. 滤镜 ----
    n_f = len(d.materials_of("filters"))
    fl = edit2.add_filter(d, d.video_segments[1].id, "清晰明亮", intensity=40)
    fmat = d.material(fl["material"])
    check("滤镜：素材 +1 且被引用", len(d.materials_of("filters")) == n_f + 1
          and fl["material"] in (d.video_segments[1].raw.get("extra_material_refs") or []),
          f"{fmat['name']} 强度 {fmat['value']}")
    check("滤镜：强度转成 0-1", abs(fmat["value"] - 0.4) < 1e-6, str(fmat["value"]))
    check("滤镜：素材形状与草稿库一致", set(fmat) >= {"effect_id", "resource_id", "apply_target_type"},
          "含 effect_id/resource_id")

    # ---- 5. 音量 ----
    seg = d.video_segments[1]
    edit2.set_gain_db(seg, -6)
    check("音量：-6dB ≈ 0.501", abs(seg.raw["volume"] - 0.5012) < 0.01, str(seg.raw["volume"]))

    d.save()
    d2 = Draft.load(case)

    # ---- 6. 删除（带前移）----
    dur_before = d2.declared_duration
    target = d2.video_segments[1]
    t_len = target.length
    edit2.remove_segment(d2, target.id, ripple=True)
    check("删除：时长减少 = 片段长度", abs((dur_before - d2.declared_duration) - t_len) < 0.02,
          f"{dur_before:.3f} -> {d2.declared_duration:.3f}（片段 {t_len:.3f}s）")
    check("删除：无重叠", verify.check_no_overlap(d2).ok)
    d2.save()
    d3 = Draft.load(case)
    check("综合：全部操作落盘后仍可读且时长为正", d3.declared_duration > 0 and len(d3.tracks) >= 4,
          f"{d3.declared_duration:.3f}s · {len(list(d3.all_segments()))} 片段")
    check("综合：原始草稿未被改动",
          base["duration"] == json.loads((src / "draft_content.json").read_text(encoding="utf-8"))["duration"],
          f"原稿 {base['duration']/1e6:.3f}s")

    if not shutil.rmtree(case, ignore_errors=True):
        pass
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
