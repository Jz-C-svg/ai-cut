#!/usr/bin/env python3
r"""重说口误 + 语义 filler 双道自测：纯算法单测 + 草稿副本端到端 + 回归。

不依赖剪映是否开着，也不碰真实草稿——端到端用 06-drafts/月子知识-成片 复制到
work/selftest/retake-*，逐字稿用合成数据注入（真实素材本批无重说/filler）。

用法:
  python selftest_retake.py
  python selftest_retake.py --keep
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, edit, verify  # noqa: E402
from aicut import retake as rt  # noqa: E402
from aicut import filler as fl  # noqa: E402
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
    ap = argparse.ArgumentParser(description="重说 + filler 双道自测")
    ap.add_argument("--draft", default="月子知识-成片")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    # ============ 1. 重说算法：整句相似 ============
    segs = [
        {"start": 0.0, "end": 1.5, "text": "今天天气很好。"},
        {"start": 2.5, "end": 4.0, "text": "今天天气很好。"},
        {"start": 4.5, "end": 6.0, "text": "我们出门吧。"},
    ]
    hits = rt.detect_retakes(segs)
    check("重说-整句命中：抓到前一遍",
          len(hits) == 1 and abs(hits[0]["remove_start"] - 0.0) < 1e-6
          and abs(hits[0]["remove_end"] - 1.55) < 1e-6,
          f"{hits}")
    check("重说-整句命中：保后一遍",
          hits and abs(hits[0]["keep_start"] - 2.5) < 1e-6 and hits[0]["reason"] == "整句相似",
          f"reason={hits[0]['reason'] if hits else None} sim={hits[0]['similarity'] if hits else None}")

    # ============ 2. 重说算法：前缀卡壳 ============
    segs2 = [
        {"start": 0.0, "end": 1.2, "text": "选月子中心啊你千万不要"},
        {"start": 2.0, "end": 3.6, "text": "选月子中心啊你千万不要被忽悠了"},
        {"start": 4.0, "end": 5.5, "text": "一定要问清楚"},
    ]
    hits2 = rt.detect_retakes(segs2)
    check("重说-前缀命中：半句卡壳被删",
          len(hits2) == 1 and hits2[0]["reason"] == "前缀相似",
          f"{hits2[0]['reason'] if hits2 else None} sim={hits2[0]['similarity'] if hits2 else None}")

    # ============ 3. 重说算法：不误杀 ============
    segs3 = [
        {"start": 0.0, "end": 1.0, "text": "今天天气很好出门散步"},
        {"start": 20.0, "end": 21.0, "text": "今天天气很好出门散步"},  # 间隔 20s > gap
    ]
    check("重说-不误杀：间隔太远不命中", rt.detect_retakes(segs3) == [])
    segs4 = [
        {"start": 0.0, "end": 0.5, "text": "嗯啊"},  # 归一化后太短 (<min_chars=5)
        {"start": 1.0, "end": 2.0, "text": "今天天气很好"},
        {"start": 2.5, "end": 3.5, "text": "今天天气很好"},
    ]
    check("重说-不误杀：短句不参与比较", len(rt.detect_retakes(segs4)) == 1)

    # ============ 4. filler：词表命中 ============
    words = [
        {"start": 0.0, "end": 0.3, "word": "嗯"},
        {"start": 0.3, "end": 0.6, "word": "啊"},
        {"start": 0.6, "end": 0.9, "word": "那个"},
        {"start": 0.9, "end": 1.5, "word": "今天"},
    ]
    fs = fl.detect_fillers(words)
    check("filler-词表：嗯/啊/那个全命中",
          len(fs) == 3 and all(r["type"] == "filler" for r in fs)
          and {r["word"] for r in fs} == {"嗯", "啊", "那个"},
          str([r["word"] for r in fs]))

    # ============ 5. filler：连续重复口吃（只删前一个） ============
    words2 = [
        {"start": 0.0, "end": 0.5, "word": "今天"},
        {"start": 0.5, "end": 0.8, "word": "今天"},
        {"start": 0.8, "end": 1.4, "word": "天气"},
    ]
    fs2 = fl.detect_fillers(words2)
    check("filler-重复：只标前一个为 repeat",
          len(fs2) == 1 and fs2[0]["type"] == "repeat" and abs(fs2[0]["start"] - 0.0) < 1e-6,
          str(fs2))

    # ============ 6. filler：多字短语按单字 token 拼接 ============
    words3 = [
        {"start": 0.0, "end": 0.2, "word": "你"},
        {"start": 0.2, "end": 0.4, "word": "知"},
        {"start": 0.4, "end": 0.6, "word": "道"},
        {"start": 0.6, "end": 0.9, "word": "吗"},
        {"start": 0.9, "end": 1.5, "word": "开始吧"},
    ]
    fs3 = fl.detect_fillers(words3)
    check("filler-多字短语：你知道吗拼成一个区间",
          len(fs3) == 1 and fs3[0]["word"] == "你知道吗"
          and abs(fs3[0]["start"] - 0.0) < 1e-6 and abs(fs3[0]["end"] - 0.9) < 1e-6,
          str(fs3))

    # ============ 7. merge_spans + 无重叠 ============
    merged = rt.merge_spans([(1.0, 2.0), (2.05, 3.0), (5.0, 6.0)])
    check("merge_spans：相邻区间合并",
          merged == [(1.0, 3.0), (5.0, 6.0)], str(merged))

    # ============ 端到端（草稿副本，合成逐字稿注入） ============
    ref = cli.resolve_draft(args.draft)
    work = ROOT / "work" / "selftest"
    ref_dur = Draft.load(ref).declared_duration

    # --- 7a. E2E 重说 ---
    case = work / "retake-e2e"
    shutil.rmtree(case, ignore_errors=True)
    shutil.copytree(ref, case)
    d1 = Draft.load(case)
    # 草稿 seg A 源范围 [2.308, 6.22] -> 时间线 [0, 3.91]，1x 速
    synth_retake = {
        "name": "synth-retake",
        "segments": [
            {"start": 3.0, "end": 4.2, "text": "选月子中心千万别被忽悠了"},
            {"start": 4.6, "end": 5.8, "text": "选月子中心千万别被忽悠了"},
        ],
        "words": [],
    }
    rep1 = run_delete_filler_retakes(mk_ctx(d1, synth_retake),
                                      {"use_retake": True, "use_filler": False,
                                       "with_silence": False, "pad_ms": 50})
    d1.save()
    after1 = Draft.load(case)
    check("E2E-重说：检测到 1 处重说并删除",
          len(rep1["channels"]["retake"]) == 1 and rep1["removed"] > 0.5,
          f"removed={rep1['removed']}s applied={rep1['applied']}")
    check("E2E-重说：时长确实缩短",
          after1.declared_duration < ref_dur - 0.5 and rep1["verify"].get("no_overlap") is True,
          f"{ref_dur:.3f} -> {after1.declared_duration:.3f}s")
    check("E2E-重说：剪后无重叠", verify.check_no_overlap(after1).ok)
    shutil.rmtree(case, ignore_errors=True)

    # --- 7b. E2E filler ---
    case = work / "filler-e2e"
    shutil.rmtree(case, ignore_errors=True)
    shutil.copytree(ref, case)
    d2 = Draft.load(case)
    synth_filler = {
        "name": "synth-filler",
        "segments": [
            {"start": 2.5, "end": 3.4, "text": "嗯 啊 那个"},
            {"start": 3.5, "end": 5.0, "text": "今天天气很好适合出门"},
        ],
        "words": [
            {"start": 2.5, "end": 2.8, "word": "嗯"},
            {"start": 2.8, "end": 3.1, "word": "啊"},
            {"start": 3.1, "end": 3.4, "word": "那个"},
            {"start": 3.6, "end": 5.0, "word": "今天天气很好适合出门"},
        ],
    }
    rep2 = run_delete_filler_retakes(mk_ctx(d2, synth_filler),
                                      {"use_retake": False, "use_filler": True,
                                       "with_silence": False, "pad_ms": 50})
    d2.save()
    after2 = Draft.load(case)
    filler_words_hit = {r["word"] for r in rep2["channels"]["filler"]}
    check("E2E-filler：嗯/啊/那个被标并删除",
          filler_words_hit == {"嗯", "啊", "那个"} and rep2["removed"] > 0.3,
          f"hit={filler_words_hit} removed={rep2['removed']}")
    check("E2E-filler：时长缩短且无重叠",
          after2.declared_duration < ref_dur and verify.check_no_overlap(after2).ok,
          f"{ref_dur:.3f} -> {after2.declared_duration:.3f}s")
    shutil.rmtree(case, ignore_errors=True)

    # --- 7c. 无词级时间戳降级 ---
    case = work / "filler-coarse"
    shutil.rmtree(case, ignore_errors=True)
    shutil.copytree(ref, case)
    d3 = Draft.load(case)
    synth_no_words = {"name": "synth-no-words", "segments": [
        {"start": 2.5, "end": 3.4, "text": "嗯啊那个"},
        {"start": 3.5, "end": 5.0, "text": "今天天气很好"},
    ]}  # 故意没有 words
    rep3 = run_delete_filler_retakes(mk_ctx(d3, synth_no_words),
                                      {"use_retake": False, "use_filler": True,
                                       "with_silence": False})
    check("降级-无词级：给出 warning 且不崩",
          any("词级" in w for w in rep3["warnings"]),
          str(rep3["warnings"]))
    shutil.rmtree(case, ignore_errors=True)

    # ============ 8. 回归：技能注册 + 内核可导入 ============
    cat = skills.catalog()
    ids = {s.id for s in cat}
    check("回归：新技能 delete_filler_retakes 已注册", "delete_filler_retakes" in ids)
    check("回归：delete_silences 向后兼容仍在", "delete_silences" in ids)
    ds = skills.get("delete_silences")
    check("回归：工程技能都有验收规格",
          all(s.verify for s in cat if s.channel == "draft"))
    check("回归：新技能 verify 含 no_overlap",
          skills.get("delete_filler_retakes").verify.get("no_overlap") is True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
