#!/usr/bin/env python3
r"""v3.7.1 模板化剪辑引擎自测：videofinger / videocompare / templateclip / find_benchmark 关键词。

纯逻辑测试，不调网络、不跑模型、不写真实素材。
用法：
  python scripts\selftest_videofinger.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import templateclip, videocompare, videofinger  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


# ================================================================ 合成档案
def _make_semantic(vtype: str = "koubo-zhishi", kps=None, summary=None,
                   zhujue="护士", changjing="月子中心", mood="专业/平静") -> dict:
    return {
        "主角": zhujue, "场景": changjing,
        "叙事摘要": summary or ["讲黄疸是什么", "讲时间规律"],
        "关键信息点": kps or ["2-3天出现", "5-7天高峰"],
        "情绪基调": mood, "适合结构": vtype,
        "高光候选": [{"t": 1.5, "理由": "开头点题"}],
    }


def _make_shotprof(shots: list[tuple[float, float, str, str]] = None) -> dict:
    """shots: (t0, t1, shot_size, shot_motion)"""
    shots = shots or [(0, 3.5, "medium", "static"), (3.5, 7.0, "close_up", "static"),
                      (7.0, 10.5, "medium", "pan"), (10.5, 14.8, "medium", "static")]
    return {
        "duration": 14.8,
        "shots": [{"t0": a, "t1": b, "len": b - a,
                   "shot_size": s, "shot_motion": m, "content": "", "emotion_tag": "neutral"}
                  for a, b, s, m in shots],
    }


def _make_transcript(segments: list[tuple[float, float, str]], duration=14.8) -> dict:
    return {"duration": duration,
            "segments": [{"start": a, "end": b, "text": t} for a, b, t in segments]}


# ================================================================ 1. from_profiles 基本装配
def test_from_profiles():
    sem = _make_semantic()
    sp = _make_shotprof()
    tp = _make_transcript([(0, 2, "这就是黄疸"), (2, 6, "2到3天出现"),
                           (6, 10, "5到7天高峰"), (10, 14, "收藏这条月子里用得上")])
    fp = videofinger.from_profiles(semantic=sem, shotprof=sp, transcript=tp, duration=14.8)
    check("from_profiles：video_type 装配", fp["video_type"] == "koubo-zhishi", fp["video_type"])
    check("from_profiles：rhythm.shot_count=4", fp["rhythm"]["shot_count"] == 4,
          str(fp["rhythm"]["shot_count"]))
    check("from_profiles：avg_shot≈3.7", abs(fp["rhythm"]["avg_shot"] - 3.7) < 0.1,
          str(fp["rhythm"]["avg_shot"]))
    check("from_profiles：subtitle.density>0.8", fp["subtitle"]["density"] > 0.8,
          str(fp["subtitle"]["density"]))
    check("from_profiles：audio.has_voiceover=True", fp["audio"]["has_voiceover"] is True)


# ================================================================ 2. classify_video_type
def test_classify_type():
    t1 = videofinger.classify_video_type(_make_semantic("duibi"), "对比一下 A 和 B")
    check("分类：duibi 结构直接映射", t1 == "duibi", t1)
    t2 = videofinger.classify_video_type(_make_semantic("jiaocheng"), "第一步怎么做")
    check("分类：jiaocheng 结构直接映射", t2 == "jiaocheng", t2)
    t3 = videofinger.classify_video_type(_make_semantic("qingxu-gushi"), "那天我突然")
    check("分类：qingxu-gushi 结构直接映射", t3 == "qingxu-gushi", t3)
    t4 = videofinger.classify_video_type(_make_semantic(""), "哪个更好？对比一下")
    check("分类：无结构标签时按关键词判 duibi", t4 == "duibi", t4)
    t5 = videofinger.classify_video_type(None, "随便说点")
    check("分类：空输入兜底 koubo-zhishi", t5 == "koubo-zhishi", t5)


# ================================================================ 3. detect_hook
def test_hook():
    segs_q = _make_transcript([(0, 2, "宝宝黄疸要不要紧？")])["segments"]
    h = videofinger.detect_hook({}, segs_q, 3.0)
    check("钩子：提问式识别", h["hook_type"] == "提问式", h["hook_type"])
    segs_p = _make_transcript([(0, 2, "千万别忽略这个信号")])["segments"]
    h = videofinger.detect_hook({}, segs_p, 3.0)
    check("钩子：痛点式识别", h["hook_type"] == "痛点式", h["hook_type"])
    segs_c = _make_transcript([(0, 2, "这就是黄疸")])["segments"]
    h = videofinger.detect_hook({}, segs_c, 3.0)
    check("钩子：陈述句兜底结论式", h["hook_type"] == "结论式", h["hook_type"])
    segs_none = _make_transcript([(0, 1, "")])["segments"]
    h = videofinger.detect_hook({}, segs_none, 3.0)
    check("钩子：空台词返回 other", h["hook_type"] == "other", h["hook_type"])


# ================================================================ 4. detect_ending
def test_ending():
    segs = _make_transcript([(11, 14, "记得收藏这条")])["segments"]
    e = videofinger.detect_ending({}, segs, 14.8, 3.0)
    check("结尾：CTA 引导识别", e["ending_type"] == "CTA引导", e["ending_type"])
    segs2 = _make_transcript([(11, 14, "你觉得呢？")])["segments"]
    e2 = videofinger.detect_ending({}, segs2, 14.8, 3.0)
    check("结尾：开放式识别", e2["ending_type"] == "开放式", e2["ending_type"])
    segs3 = _make_transcript([(11, 14, "总之希望大家都好")])["segments"]
    e3 = videofinger.detect_ending({}, segs3, 14.8, 3.0)
    check("结尾：总结升华识别", e3["ending_type"] == "总结升华", e3["ending_type"])


# ================================================================ 5. build 缓存读写（临时目录）
def test_build_cache(tmp_path=None):
    import tempfile, os
    from aicut import paths as P
    # 用临时目录绕开真实 work/
    tmp = Path(tempfile.mkdtemp(prefix="vf-cache-"))
    try:
        sem = _make_semantic()
        sp = _make_shotprof()
        tp = _make_transcript([(0, 2, "这就是黄疸"), (2, 6, "2到3天出现")])
        fp = videofinger.from_profiles(semantic=sem, shotprof=sp, transcript=tp,
                                       video="x.mp4", duration=14.8)
        out = tmp / "fingerprint-x.json"
        videofinger.save(fp, out)
        loaded = videofinger.load(out)
        check("save/load 缓存读写一致", loaded["video_type"] == fp["video_type"]
              and loaded["rhythm"]["shot_count"] == fp["rhythm"]["shot_count"])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ================================================================ 6. compare 同指纹高分
def test_compare_same():
    fp = videofinger.from_profiles(
        semantic=_make_semantic(), shotprof=_make_shotprof(),
        transcript=_make_transcript([(0, 3, "这就是黄疸"), (3, 7, "2到3天出现"),
                                      (7, 11, "5到7天高峰"), (11, 14, "收藏")]),
        duration=14.8)
    r = videocompare.compare(fp, fp)
    check("compare：同指纹 total_score≥0.9", r["total_score"] >= 0.9,
          str(r["total_score"]))
    check("compare：type 维度=1.0", r["dimensions"]["type"]["score"] == 1.0)


# ================================================================ 7. compare 不同类型低分
def test_compare_diff_type():
    fp1 = videofinger.from_profiles(
        semantic=_make_semantic("koubo-zhishi", kps=["黄疸", "新生儿"]),
        shotprof=_make_shotprof(),
        transcript=_make_transcript([(0, 3, "这就是黄疸"), (3, 7, "2到3天出现")]),
        duration=14.8)
    fp2 = videofinger.from_profiles(
        semantic=_make_semantic("qingxu-gushi", kps=["那天我哭了", "后来想通了"],
                                summary=["一段往事", "感悟"]),
        shotprof=_make_shotprof([(0, 5.0, "wide", "handheld"), (5.0, 12.0, "close_up", "pan")]),
        transcript=_make_transcript([(0, 5, "那天突然下雨"), (5, 12, "我站在街角")]),
        duration=12.0)
    r = videocompare.compare(fp1, fp2)
    check("compare：不同类型 total_score≤0.55", r["total_score"] <= 0.55,
          str(r["total_score"]))
    check("compare：type 维度=0.2", r["dimensions"]["type"]["score"] == 0.2)


# ================================================================ 8. rank 排序正确性
def test_rank():
    q = videofinger.from_profiles(
        semantic=_make_semantic(), shotprof=_make_shotprof(),
        transcript=_make_transcript([(0, 3, "黄疸"), (3, 7, "2到3天")]), duration=14.8)
    good = videofinger.from_profiles(
        semantic=_make_semantic(), shotprof=_make_shotprof(),
        transcript=_make_transcript([(0, 3, "黄疸"), (3, 7, "2到3天")]), duration=14.8)
    bad = videofinger.from_profiles(
        semantic=_make_semantic("qingxu-gushi", kps=["回忆"], summary=["故事"]),
        shotprof=_make_shotprof([(0, 8, "wide", "handheld")]),
        transcript=_make_transcript([(0, 8, "回忆那天")]), duration=8.0)
    ranked = videocompare.rank(q, [bad, good])
    check("rank：同类型排第一", ranked[0]["total_score"] >= ranked[1]["total_score"],
          f"{ranked[0]['total_score']} vs {ranked[1]['total_score']}")
    check("rank：返回列表带 total_score", all("total_score" in x for x in ranked))


# ================================================================ 9. extract_template
def test_extract_template():
    fp = videofinger.from_profiles(
        semantic=_make_semantic(), shotprof=_make_shotprof(),
        transcript=_make_transcript([(0, 2, "宝宝黄疸别大意"), (2, 6, "2到3天出现"),
                                      (6, 10, "5到7天高峰"), (10, 14, "收藏这条")]),
        duration=14.8)
    t = templateclip.extract_template(fp, template_id="test-tmpl", template_name="测试模板")
    check("extract_template：template_id 保留", t["template_id"] == "test-tmpl")
    check("extract_template：rhythm.target_avg_shot>0", t["rhythm"]["target_avg_shot"] > 0)
    check("extract_template：min<max", t["rhythm"]["min_shot"] < t["rhythm"]["max_shot"])
    check("extract_template：style_hint=koubo-zhishi", t["style_hint"] == "koubo-zhishi")
    check("extract_template：techniques.subtitle_max_chars>0",
          t["techniques"]["subtitle_max_chars"] > 0)


# ================================================================ 10. apply_template
def test_apply_template():
    ref = videofinger.from_profiles(
        semantic=_make_semantic(), shotprof=_make_shotprof(),
        transcript=_make_transcript([(0, 2, "别大意"), (2, 6, "2到3天"), (6, 10, "高峰"), (10, 14, "收藏")]),
        duration=14.8)
    mat = videofinger.from_profiles(
        semantic=_make_semantic(kps=["两周内消退"]), shotprof=_make_shotprof(),
        transcript=_make_transcript([(0, 2, "这就是黄疸"), (2, 6, "2到3天"), (6, 14, "收藏")]),
        duration=14.8)
    t = templateclip.extract_template(ref, template_id="t1")
    applied = templateclip.apply_template(t, mat)
    check("apply_template：short<long", applied["short_seconds"] < applied["long_seconds"])
    check("apply_template：cover_hint 来自素材", "两周内消退" in applied["cover_hint"]
          or "黄疸" in applied["cover_hint"], applied["cover_hint"])
    check("apply_template：template_used=t1", applied["template_used"] == "t1")
    check("apply_template：style=koubo-zhishi", applied["style"] == "koubo-zhishi")


# ================================================================ 11. save/load 模板持久化（临时目录）
def test_template_persist():
    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp(prefix="tmpl-"))
    try:
        t = {"template_id": "persist-test", "template_name": "持久化测试",
             "style_hint": "koubo-zhishi", "rhythm": {"target_avg_shot": 3.5, "min_shot": 1.5, "max_shot": 6.0},
             "techniques": {"subtitle_max_chars": 14, "keyframe_mode": "slow-zoom"}}
        p = tmp / "persist-test.json"
        templateclip.save_template(t, p)
        loaded = templateclip.load_template(str(p))
        check("模板 save/load 一致", loaded["template_id"] == "persist-test"
              and loaded["rhythm"]["target_avg_shot"] == 3.5)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ================================================================ 12. find_benchmark 关键词生成（mock 指纹）
def test_search_keywords():
    sys.path.insert(0, str(ROOT / "scripts"))
    import find_benchmark  # noqa: E402
    fp = videofinger.from_profiles(
        semantic=_make_semantic(kps=["新生儿黄疸"], zhujue="护士", changjing="月子中心"),
        shotprof=_make_shotprof(),
        transcript=_make_transcript([(0, 2, "黄疸"), (2, 6, "2到3天")]), duration=14.8)
    kws = find_benchmark.build_search_keywords(fp)
    check("关键词生成：返回非空列表", isinstance(kws, list) and len(kws) >= 2)
    check("关键词生成：含主题词", any("黄疸" in k for k in kws), str(kws))
    check("关键词生成：含类型词", any("口播" in k or "科普" in k for k in kws))


# ================================================================ 主函数
def main() -> int:
    test_from_profiles()
    test_classify_type()
    test_hook()
    test_ending()
    test_build_cache()
    test_compare_same()
    test_compare_diff_type()
    test_rank()
    test_extract_template()
    test_apply_template()
    test_template_persist()
    test_search_keywords()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
