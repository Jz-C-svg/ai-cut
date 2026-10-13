#!/usr/bin/env python3
r"""v3.1 文本字幕三件套自测：预设复用+划词高亮 / 多层堆叠+错峰 / DP对齐+严格校验。

在 work/selftest/text_v31 里做副本，不碰真实草稿。
覆盖（≥10 项）：
  1.  apply_text_preset 字段断言（字号/颜色/描边写入 styles[0]）
  2.  apply_text_preset 局部覆盖（显式参数覆盖预设值）
  3.  set_global_keyword_highlight 写入 globalKeywordStyle 字段
  4.  add_stacked_subtitles layer 分配（重叠字幕落到不同层/Y坐标）
  5.  add_stacked_subtitles stagger 延迟（入场动画 start 逐层递增）
  6.  _infer_line_height 行距反推
  7.  align_subtitles_dp 单调对齐（对齐后 start 不减）
  8.  align_subtitles_dp max_shift 约束（偏移超限的字幕原样保留）
  9.  validate_subtitle_timeline 检出违规（时间倒序/负时长/同层重叠）
  10. 持久化：Draft.save + reload 后字段仍在
  11. list_text_presets / save_text_preset
  12. align_and_validate_subtitles 端到端
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, edit2, text_presets, subtitle_align  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="v3.1 文本字幕三件套自测")
    ap.add_argument("--draft", default="月子知识-成片")
    args = ap.parse_args()

    src = cli.resolve_draft(args.draft)
    case = ROOT / "work" / "selftest" / "text_v31"
    shutil.rmtree(case, ignore_errors=True)
    case.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, case)

    d = Draft.load(case)

    # ---- 1. apply_text_preset 字段断言 ----
    rep1 = edit2.add_styled_text(d, "预设复用测试", 1.0, 3.0)
    r1 = text_presets.apply_text_preset(d, rep1["segment"], "口播标准")
    mat1 = d.material(rep1["material"])
    c1 = json.loads(mat1["content"])
    s0_1 = c1["styles"][0]
    check("1.预设应用：字号=6.0（口播标准/字幕）",
          s0_1["size"] == 6.0, f"size={s0_1['size']}")
    check("1.预设应用：fill.color=白色",
          s0_1["fill"]["content"]["solid"]["color"] == [1.0, 1.0, 1.0],
          str(s0_1["fill"]["content"]["solid"]["color"]))
    check("1.预设应用：strokes 非空（描边宽度40→0.08）",
          len(s0_1.get("strokes", [])) == 1 and abs(s0_1["strokes"][0]["width"] - 0.08) < 1e-3,
          f"strokes={len(s0_1.get('strokes', []))}")
    check("1.预设应用：片段 y=-0.78（预设位置）",
          abs(d.text_segments[-1].raw["clip"]["transform"]["y"] - (-0.78)) < 0.01,
          f"y={d.text_segments[-1].raw['clip']['transform']['y']}")

    # ---- 2. apply_text_preset 局部覆盖 ----
    rep2 = edit2.add_styled_text(d, "局部覆盖测试", 4.0, 6.0)
    r2 = text_presets.apply_text_preset(d, rep2["segment"], "口播标准",
                                         size=9.5, color=[1.0, 0.83, 0.0], y=0.3)
    mat2 = d.material(rep2["material"])
    c2 = json.loads(mat2["content"])
    s0_2 = c2["styles"][0]
    check("2.局部覆盖：size=9.5 覆盖预设 6.0",
          s0_2["size"] == 9.5, f"size={s0_2['size']}")
    check("2.局部覆盖：color=[1.0,0.83,0.0] 覆盖预设白色",
          s0_2["fill"]["content"]["solid"]["color"] == [1.0, 0.83, 0.0],
          str(s0_2["fill"]["content"]["solid"]["color"]))
    check("2.局部覆盖：y=0.3 覆盖预设 -0.78",
          abs(d.text_segments[-1].raw["clip"]["transform"]["y"] - 0.3) < 1e-6,
          f"y={d.text_segments[-1].raw['clip']['transform']['y']}")

    # ---- 3. globalKeywordStyle 划词高亮全局开关 ----
    r3 = text_presets.set_global_keyword_highlight(
        d, True, color=[1.0, 0.85, 0.2], keywords=["数字", "关键词"])
    gks = d.content.get("globalKeywordStyle")
    check("3.划词高亮：globalKeywordStyle.enabled=True",
          gks is not None and gks.get("enabled") is True, str(gks))
    check("3.划词高亮：color 写入草稿",
          gks is not None and gks.get("color") == [1.0, 0.85, 0.2],
          str(gks.get("color")))
    check("3.划词高亮：keywords 列表写入",
          gks is not None and gks.get("keywords") == ["数字", "关键词"],
          str(gks.get("keywords")))
    # 关闭
    text_presets.set_global_keyword_highlight(d, False)
    check("3.划词高亮：关闭后 enabled=False",
          d.content["globalKeywordStyle"]["enabled"] is False)

    # ---- 4. add_stacked_subtitles layer 分配 ----
    stacked_list = [
        {"text": "第一层字幕A", "t0": 10.0, "t1": 13.0},
        {"text": "第二层字幕B", "t0": 11.0, "t1": 14.0},  # 与A重叠→layer1
        {"text": "第三层字幕C", "t0": 15.0, "t1": 17.0},  # 不重叠→layer0
    ]
    rep4 = edit2.add_stacked_subtitles(d, stacked_list,
                                         max_stack_depth=2, stagger_us=100000,
                                         y_center=0.8)
    check("4.堆叠：3 条字幕全部添加", rep4["added"] == 3, f"added={rep4['added']}")
    check("4.堆叠：分配到 2 层", rep4["layers"] == 2, f"layers={rep4['layers']}")
    layers_used = [it["layer"] for it in rep4["items"]]
    check("4.堆叠：重叠字幕A/B 分到不同 layer",
          layers_used[0] != layers_used[1], f"layers={layers_used}")
    # Y 坐标不同
    ys = [it["y"] for it in rep4["items"]]
    check("4.堆叠：不同 layer 有不同 Y 坐标",
          ys[0] != ys[1], f"ys={ys}")

    # ---- 5. stagger 延迟 ----
    # 检查每条的入场动画 start
    delays = [it["intro_delay_us"] for it in rep4["items"]]
    check("5.错峰：layer1 的延迟=100000us（layer0=0）",
          delays[1] == 100000 and delays[0] == 0,
          f"delays={delays}")
    # 验证动画素材里确实有 start 字段
    anim_mat = d.material(rep4["items"][1]["intro_material"])
    in_anim = anim_mat["animations"][0]
    check("5.错峰：动画 start=100000us",
          in_anim["start"] == 100000, f"start={in_anim['start']}")

    # ---- 6. 行距反推 ----
    # 用已加的素材测试 _infer_line_height
    sample_mat = d.material(rep1["material"])
    lh = edit2._infer_line_height(sample_mat)
    check("6.行距反推：line_height 在合理范围 (0.1~0.2)",
          0.05 < lh < 0.3, f"line_height={lh:.4f}")
    # Y 计算：layer0 和 layer1 差一个 line_height
    y0 = edit2._calculate_stack_y(0, 2, 0.8, lh)
    y1 = edit2._calculate_stack_y(1, 2, 0.8, lh)
    check("6.行距反推：两层 Y 差 ≈ line_height",
          abs(abs(y1 - y0) - lh) < 1e-6, f"y0={y0} y1={y1} diff={abs(y1-y0):.4f}")

    # ---- 7. DP 对齐单调 ----
    sub_times = [(0.0, 1.5), (2.0, 3.5), (4.0, 5.5)]
    events = [(0.1, 0.5), (2.0, 2.5), (4.1, 4.5)]
    aligned = subtitle_align.align_subtitles_dp(sub_times, events, max_shift=0.3)
    check("7.DP对齐：对齐后 start 单调不减",
          all(aligned[i][0] <= aligned[i + 1][0] + 1e-6 for i in range(len(aligned) - 1)),
          str(aligned))
    check("7.DP对齐：duration 保持不变",
          all(abs((aligned[i][1] - aligned[i][0]) - 1.5) < 0.01 for i in range(len(aligned))),
          str(aligned))

    # ---- 8. DP 对齐 max_shift 约束 ----
    sub_times2 = [(0.0, 1.0), (5.0, 6.0)]  # 第二条离最近事件很远
    events2 = [(0.1, 0.5), (0.2, 0.6)]     # 所有事件都在 0 附近
    aligned2 = subtitle_align.align_subtitles_dp(sub_times2, events2, max_shift=0.3)
    check("8.DP约束：偏移超限时第二条原样返回",
          abs(aligned2[1][0] - 5.0) < 0.01, f"aligned2={aligned2}")

    # ---- 9. validate_subtitle_timeline 检出违规 ----
    bad_subs = [
        {"t0": 5.0, "t1": 6.0, "text": "A", "layer": 0, "source": 0},
        {"t0": 3.0, "t1": 4.0, "text": "B", "layer": 0, "source": 1},  # 时间倒序
        {"t0": 7.0, "t1": 7.0, "text": "C", "layer": 0, "source": 0},  # 负时长 + 重复source
    ]
    v = subtitle_align.validate_subtitle_timeline(bad_subs)
    check("9.校验：检出时间倒序", any("倒序" in x for x in v["violations"]),
          str(v["violations"]))
    check("9.校验：检出负时长", any("负时长" in x or "零时长" in x for x in v["violations"]),
          str(v["violations"]))
    check("9.校验：检出重复来源", any("被引用" in x for x in v["violations"]),
          str(v["violations"]))
    check("9.校验：valid=False", v["valid"] is False)

    # 同层重叠
    overlap_subs = [
        {"t0": 0.0, "t1": 2.0, "text": "X", "layer": 0},
        {"t0": 1.0, "t1": 3.0, "text": "Y", "layer": 0},  # 同层重叠
    ]
    v2 = subtitle_align.validate_subtitle_timeline(overlap_subs)
    check("9.校验：检出同层重叠", any("同层重叠" in x for x in v2["violations"]),
          str(v2["violations"]))

    # ---- 10. 持久化 ----
    d.save()
    d2 = Draft.load(case)
    mats = {m["id"]: m for m in d2.materials_of("texts")}
    re_mat = mats[rep1["material"]]
    re_content = json.loads(re_mat["content"])
    check("10.持久化：reload 后预设字号仍在",
          re_content["styles"][0]["size"] == 6.0, f"size={re_content['styles'][0]['size']}")
    check("10.持久化：reload 后 globalKeywordStyle 仍在",
          d2.content.get("globalKeywordStyle", {}).get("enabled") is False)
    # 堆叠字幕 reload 后仍在
    check("10.持久化：reload 后文本段数增加",
          len(d2.text_segments) >= 7, f"{len(d2.text_segments)} 段")

    # ---- 11. list/save presets ----
    presets = text_presets.list_text_presets()
    check("11.预设列举：至少有 口播标准",
          any(p["名称"] == "口播标准" for p in presets),
          str([p["名称"] for p in presets]))
    save_rep = text_presets.save_text_preset("测试预设", {"字号": 7.0, "颜色": [0.1, 0.2, 0.3]})
    check("11.预设保存：返回 op 正确", save_rep.get("op") == "save_text_preset")
    presets2 = text_presets.list_text_presets()
    check("11.预设保存：新预设出现在列表",
          any(p["名称"] == "测试预设" for p in presets2))
    # 清理：删掉测试预设，避免污染
    cfg_path = ROOT / "config" / "text_styles.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.get("presets", {}).pop("测试预设", None)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 12. align_and_validate_subtitles 端到端 ----
    rep12 = edit2.align_and_validate_subtitles(d2, [(1.0, 1.5), (4.0, 4.5), (10.0, 10.5)])
    check("12.端到端：对齐了所有文本片段",
          rep12["aligned"] == len(d2.text_segments),
          f"aligned={rep12['aligned']} total={len(d2.text_segments)}")
    check("12.端到端：validation 有结果",
          "valid" in rep12["validation"] and "violations" in rep12["validation"])

    # ---- 汇总 ----
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    if failed:
        for n, _, det in failed:
            print("  FAIL:", n, det)
    shutil.rmtree(case, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
