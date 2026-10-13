#!/usr/bin/env python3
r"""v3.2 融合选优自测：A3 重复短语 + A2 口误分类/review_mode + A1 capcut_auto + A5 Stop Gate。

纯算法单测 + 草稿副本端到端（06-drafts/月子知识-成片 复制到 work/selftest/v32-*）。
不碰真实草稿，不调付费 API。

用法:
  python selftest_v32_fusion.py
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, verify  # noqa: E402
from aicut import retake as rt  # noqa: E402
from aicut import capcut_auto as ca  # noqa: E402
from aicut import skills  # noqa: E402
from aicut.draft import Draft  # noqa: E402
from aicut.skills_retake import run_delete_filler_retakes  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def mk_ctx(draft: Draft, transcript: dict | None, dry: bool = False) -> skills.Context:
    return skills.Context(draft=draft, transcript=transcript, dry_run=dry, log=print)


def main() -> int:
    ap = argparse.ArgumentParser(description="v3.2 融合选优自测")
    ap.add_argument("--draft", default="月子知识-成片")
    args = ap.parse_args()

    # ============ A3.1 重复短语：命中前一次 ============
    segs = [{"start": 0.0, "end": 2.2, "text": "我们今天我们今天去公园"}]
    hits = rt.detect_repeated_phrases(segs)
    check("A3-重复短语：命中前一次并保后一次",
          len(hits) == 1 and hits[0]["phrase"] == "我们今天"
          and abs(hits[0]["remove_start"] - 0.0) < 1e-6
          and abs(hits[0]["remove_end"] - 0.8) < 1e-6
          and abs(hits[0]["keep_start"] - 0.8) < 1e-6
          and hits[0]["occurrence_count"] == 2,
          f"{hits}")

    # ============ A3.2 重复短语：间隔太远不误杀 ============
    segs_far = [{"start": 0.0, "end": 1.0, "text": "今天天气很好"},
                {"start": 15.0, "end": 16.0, "text": "今天天气很好"}]
    check("A3-重复短语：间隔>5s 不判定为口误",
          rt.detect_repeated_phrases(segs_far) == [])

    # ============ A3.3 重复短语：正常文本不误杀 ============
    segs_ok = [{"start": 0.0, "end": 3.0, "text": "今天天气很好适合出门散步晒太阳"}]
    check("A3-重复短语：正常文本不命中",
          rt.detect_repeated_phrases(segs_ok) == [])

    # ============ A3.4 与句级 Levenshtein 取并集不冲突 ============
    segs_both = [
        {"start": 0.0, "end": 1.5, "text": "今天天气很好"},
        {"start": 2.5, "end": 4.0, "text": "今天天气很好"},
    ]
    union = rt.detect_retakes(segs_both) + rt.detect_repeated_phrases(segs_both)
    check("A3-并集：句级+短语级都能抓到（至少一道命中）",
          len(union) >= 1, f"retake={len(rt.detect_retakes(segs_both))} rp={len(rt.detect_repeated_phrases(segs_both))}")

    # ============ A2.1 口误分类：四类正确 ============
    c_rep = rt.classify_disfluency({"reason": "重复短语"})
    c_sent = rt.classify_disfluency({"reason": "整句相似"})
    c_stall = rt.classify_disfluency({"reason": "前缀相似"})
    c_fill = rt.classify_disfluency({"type": "filler", "word": "那个"})
    c_sil = rt.classify_disfluency({"kind": "silence"})
    check("A2-分类：重复/卡顿/语气词/长静音 四类正确",
          c_rep == "重复" and c_sent == "重复" and c_stall == "卡顿"
          and c_fill == "语气词" and c_sil == "长静音",
          f"{c_rep}/{c_sent}/{c_stall}/{c_fill}/{c_sil}")

    # ============ 端到端（草稿副本） ============
    ref = cli.resolve_draft(args.draft)
    work = ROOT / "work" / "selftest"
    ref_dur = Draft.load(ref).declared_duration

    # --- A2.2 review_mode：返回区间但不删 ---
    case = work / "v32-review"
    shutil.rmtree(case, ignore_errors=True)
    shutil.copytree(ref, case)
    d = Draft.load(case)
    before_n = len(list(d.all_segments()))
    synth = {"name": "synth", "segments": [
        {"start": 2.5, "end": 3.6, "text": "我们今天我们今天讲月子"},
        {"start": 4.0, "end": 5.5, "text": "选月子中心千万别被忽悠了"},
    ]}
    rep = run_delete_filler_retakes(mk_ctx(d, synth),
                                    {"use_retake": True, "use_filler": False,
                                     "use_repeated_phrases": True, "with_silence": False,
                                     "review_mode": True, "pad_ms": 50})
    after_n = len(list(d.all_segments()))
    check("A2-review_mode：返回待删区间且未改草稿",
          rep.get("review_mode") is True and rep["applied"] == [] and rep["removed"] == 0.0
          and before_n == after_n and len(rep["planned_to_delete"]) >= 1,
          f"planned={len(rep['planned_to_delete'])} segs {before_n}->{after_n}")
    # review_mode 下 repeated_phrases 通道应命中"我们今天"
    rp_hit = rep["channels"].get("repeated_phrases", [])
    check("A2-review_mode：repeated_phrases 通道命中并带分类",
          len(rp_hit) >= 1 and rp_hit[0].get("disfluency") == "重复"
          and rp_hit[0]["phrase"].startswith("我们今天"),
          f"{rp_hit[:1]}")

    # --- A3.5 E2E：repeated_phrases 真的删了 ---
    case2 = work / "v32-rp-delete"
    shutil.rmtree(case2, ignore_errors=True)
    shutil.copytree(ref, case2)
    d2 = Draft.load(case2)
    rep2 = run_delete_filler_retakes(mk_ctx(d2, synth),
                                      {"use_retake": False, "use_filler": False,
                                       "use_repeated_phrases": True, "with_silence": False,
                                       "pad_ms": 50})
    d2.save()
    after2 = Draft.load(case2)
    check("A3-E2E：重复短语真的删掉且时长缩短",
          rep2["removed"] >= 0.25 and after2.declared_duration < ref_dur - 0.2,
          f"removed={rep2['removed']} dur {ref_dur:.2f}->{after2.declared_duration:.2f}")
    check("A3-E2E：剪后无重叠", verify.check_no_overlap(after2).ok)
    shutil.rmtree(case, ignore_errors=True)
    shutil.rmtree(case2, ignore_errors=True)

    # --- A1.1/A1.2/A1.3：duplicate_as_template + replace_text + inspect_material ---
    case3 = work / "v32-capcut"
    shutil.rmtree(case3, ignore_errors=True)
    shutil.copytree(ref, case3)
    d3 = Draft.load(case3)
    text_segs = d3.text_segments
    if not text_segs:
        check("A1-前置：草稿有文本片段", False, "无 text 片段，跳过 A1")
    else:
        orig = text_segs[0]
        orig_mat = d3.material(orig.material_id)
        # 存原始 styles[0] 的 font/size/fill 指纹
        import json as _json
        orig_cj = _json.loads(orig_mat["content"]) if isinstance(orig_mat.get("content"), str) else {}
        orig_style0 = _json.dumps(orig_cj.get("styles", [{}])[0], sort_keys=True, ensure_ascii=False)

        # duplicate_as_template
        new_sid = ca.duplicate_as_template(d3, orig.id)
        new_seg = next(s for s in d3.all_segments() if s.id == new_sid)
        new_mat = d3.material(new_seg.material_id)
        new_cj = _json.loads(new_mat["content"]) if isinstance(new_mat.get("content"), str) else {}
        # 比较 styles[0]（剔除 range，因为新文本为空 range=[0,0]）
        def _style0_no_range(cj):
            s0 = _json.loads(_json.dumps((cj.get("styles") or [{}])[0]))
            s0.pop("range", None)
            return _json.dumps(s0, sort_keys=True, ensure_ascii=False)
        check("A1-duplicate_as_template：新片段文本为空且样式保留",
              new_cj.get("text") == ""
              and _style0_no_range(new_cj) == _style0_no_range(orig_cj),
              f"new_text={new_cj.get('text')!r}")

        # replace_text：文本变了但 styles[0] 的 font/size/fill 不变
        NEW_TEXT = "替换后的新字幕ABC"
        ok = ca.replace_text(d3, new_sid, NEW_TEXT)
        after_mat = d3.material(new_seg.material_id)
        after_cj = _json.loads(after_mat["content"])
        after_style0 = after_cj.get("styles", [{}])[0]
        check("A1-replace_text：文本变了但 styles[0] font/size/fill 不变",
              ok and after_cj.get("text") == NEW_TEXT
              and after_style0.get("size") == (orig_cj.get("styles", [{}])[0].get("size"))
              and _json.dumps(after_style0.get("fill"), sort_keys=True) ==
                  _json.dumps((orig_cj.get("styles", [{}])[0].get("fill")), sort_keys=True)
              and after_style0.get("range") == [0, len(NEW_TEXT)],
              f"text={after_cj.get('text')!r} size={after_style0.get('size')} range={after_style0.get('range')}")

        # inspect_material：返回结构
        info = ca.inspect_material(d3, orig.id)
        check("A1-inspect_material：返回结构化信息（含 material_id / 字体 / extra_refs）",
             isinstance(info, dict) and info.get("segment_id") == orig.id
             and "extra_refs" in info,
             f"keys={list(info.keys())}")

    # --- A1.4 import_track ---
    case4 = work / "v32-import"
    shutil.rmtree(case4, ignore_errors=True)
    shutil.copytree(ref, case4)
    d4 = Draft.load(case4)
    n_before = len(d4.text_segments)
    imported = ca.import_track(d4, ref, "text")
    check("A1-import_track：从源草稿导入文本轨道片段",
          imported > 0 and len(d4.text_segments) == n_before + imported,
          f"imported={imported} segs {n_before}->{len(d4.text_segments)}")
    d4.save()
    reloaded = Draft.load(case4)
    check("A1-import_track：导入后可重新 load 且无悬空引用",
          len(reloaded.text_segments) == n_before + imported
          and verify.stop_gate_check(reloaded)["passed"],
          f"gate passed={verify.stop_gate_check(reloaded)['passed']}")
    shutil.rmtree(case3, ignore_errors=True)
    shutil.rmtree(case4, ignore_errors=True)

    # --- A5.1 stop_gate：正常草稿通过 ---
    case5 = work / "v32-gate-ok"
    shutil.rmtree(case5, ignore_errors=True)
    shutil.copytree(ref, case5)
    d5 = Draft.load(case5)
    g_ok = verify.stop_gate_check(d5)
    check("A5-stop_gate：正常草稿通过", g_ok["passed"],
          f"violations={g_ok['violations'][:2]}")
    shutil.rmtree(case5, ignore_errors=True)

    # --- A5.2 stop_gate：悬空引用能检出（进 warnings） ---
    case6 = work / "v32-gate-bad"
    shutil.rmtree(case6, ignore_errors=True)
    shutil.copytree(ref, case6)
    d6 = Draft.load(case6)
    # 给第一个片段的 extra_material_refs 塞一个不存在的 id
    first = next(iter(d6.all_segments()))
    first.raw.setdefault("extra_material_refs", []).append("FAKE_DANGLING_ID_123")
    g_bad = verify.stop_gate_check(d6)
    check("A5-stop_gate：悬空引用被检出（warning）",
          any("FAKE_DAN" in w for w in g_bad["warnings"]),
          f"{g_bad['warnings'][:1]}")
    shutil.rmtree(case6, ignore_errors=True)

    # --- A5.2b stop_gate：主素材缺失硬阻断 ---
    case6b = work / "v32-gate-noprim"
    shutil.rmtree(case6b, ignore_errors=True)
    shutil.copytree(ref, case6b)
    d6b = Draft.load(case6b)
    prim = next(iter(d6b.all_segments()))
    prim.raw["material_id"] = "NONEXISTENT_PRIMARY_MAT"
    g_noprim = verify.stop_gate_check(d6b)
    check("A5-stop_gate：主素材缺失硬阻断 passed=False",
          (not g_noprim["passed"]) and any("悬空主素材" in v for v in g_noprim["violations"]),
          f"{g_noprim['violations'][:1]}")
    shutil.rmtree(case6b, ignore_errors=True)

    # --- A5.3 stop_gate：越界片段能检出 ---
    case7 = work / "v32-gate-oob"
    shutil.rmtree(case7, ignore_errors=True)
    shutil.copytree(ref, case7)
    d7 = Draft.load(case7)
    last_seg = list(d7.all_segments())[-1]
    # 把它的 end 推到很远（远超时长）
    rng = last_seg.raw["target_timerange"]
    rng["duration"] = rng.get("duration", 1000000) + 100 * 1000000  # +100s
    g_oob = verify.stop_gate_check(d7)
    check("A5-stop_gate：越界片段被检出",
          (not g_oob["passed"]) and any("越界" in v for v in g_oob["violations"]),
          f"{g_oob['violations'][:2]}")
    shutil.rmtree(case7, ignore_errors=True)

    # ============ 回归：技能注册 ============
    cat = {s.id for s in skills.catalog()}
    check("回归：A1 四个新技能已注册",
          {"duplicate_as_template", "replace_text", "inspect_material",
           "import_track"} <= cat)
    check("回归：delete_filler_retakes 仍在且带新参数",
          "delete_filler_retakes" in cat
          and "use_repeated_phrases" in skills.get("delete_filler_retakes").args
          and "review_mode" in skills.get("delete_filler_retakes").args)
    check("回归：句级 detect_retakes 未被破坏",
          len(rt.detect_retakes([{"start": 0, "end": 1, "text": "今天天气很好"},
                                 {"start": 2, "end": 3, "text": "今天天气很好"}])) == 1)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败项：")
        for n, _, d in failed:
            print(f"  - {n} · {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
