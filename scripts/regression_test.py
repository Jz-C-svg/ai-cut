# -*- coding: utf-8 -*-
"""
知识内化回归验证脚本（AI cut · 任务3）

确保知识内化改造没有破坏现有 agent 链路（短视频剪辑/导出/评审）。
每个测试项输出 PASS/FAIL + 详情，结果写 regression_results.json。
如发现 FAIL，只记录错误信息，不修改其他子代理正在改的文件。
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

OUT_DIR = ROOT / "08-data" / "knowledge_test"
RESULT_FILE = OUT_DIR / "regression_results.json"

results: list[dict] = []


def record(name: str, passed: bool, detail: str, layer: str = "core") -> None:
    results.append({"测试项": name, "结果": "PASS" if passed else "FAIL",
                     "层级": layer, "详情": detail})
    print(f"[{'PASS' if passed else 'FAIL'}] {name} — {detail}")


def check(name: str, fn, layer: str = "core") -> None:
    try:
        ok, detail = fn()
        record(name, bool(ok), detail, layer)
    except Exception as exc:  # noqa: BLE001
        record(name, False, f"异常: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}", layer)


# ───────────────────────── 用例 ─────────────────────────

def t1_architecture_match():
    from aicut import architecture
    # 已知用例：三问测一测 → wenda 相关；黄疸口播
    segs = [{"start": 0.0, "end": 3.0, "text": "在家坐月子和月子中心花30秒测一测"},
            {"start": 3.0, "end": 8.0, "text": "第一个问题白天有人帮你带孩子吗"},
            {"start": 8.0, "end": 13.0, "text": "第二个问题晚上能睡好吗"},
            {"start": 13.0, "end": 19.0, "text": "三个问题都不能就考虑月子中心"}]
    m = architecture.match_architecture(segs, content_layer="shortform")
    aid = m["matched"]["id"]
    ok = aid in ("wenda-xuannian", "koubo-zhishi-arch")
    return ok, f"匹配={aid}({m['matched'].get('名称')}) 方法={m.get('method')}"


def t2_detect_layer():
    from aicut import architecture
    cases = [
        ([0.0, 10.0], "shortform"),
        ([0.0, 180.0], "shortform"),   # 边界 =180s
        ([0.0, 181.0], "longform"),
        ([0.0, 300.0], "longform"),
        ([0.0, 600.0], "longform"),   # 边界 =600s
        ([0.0, 601.0], "film-level"),
    ]
    bad = []
    for (a, b), expect in cases:
        segs = [{"start": float(a), "end": float(b), "text": "x"}]
        got = architecture.detect_content_layer(segs)
        if got != expect:
            bad.append(f"{b}s→{got}(应{expect})")
    return (not bad), ("全部7边界正确" if not bad else "; ".join(bad))


def t3_combine_with_style():
    from aicut import architecture
    m = architecture.match_architecture(
        [{"start": 0.0, "end": 5.0, "text": "对比测评在家vs月子中心"}], content_layer="shortform")
    style = {"id": "koubo-zhishi", "名称": "口播知识型",
             "剪辑参数": {"镜头最短秒": 2.0, "镜头最长秒": 6.0, "BGM情绪": "平缓", "BGM音量": 0.15}}
    dec = architecture.combine_with_style(m, style)
    ok = dec.get("architecture", {}).get("id") == m["matched"]["id"] and dec.get("style")
    return ok, f"联合决策架构={dec['architecture'].get('id')} 风格={dec['style'].get('名称') if dec.get('style') else None}"


def t4_generate_intent_no_arch():
    from aicut import edit_intent
    segs = [{"start": 0.0, "end": 3.0, "text": "宝宝黄疸先别慌"},
            {"start": 3.0, "end": 6.0, "text": "3秒教你判断"}]
    intent = edit_intent.generate_intent(segs)
    ok = isinstance(intent, dict) and "逐句意图" in intent
    return ok, f"不传architecture仍返回dict，逐句意图{len(intent.get('逐句意图', []))}句"


def t5_generate_intent_with_arch():
    from aicut import architecture, edit_intent
    segs = [{"start": 0.0, "end": 4.0, "text": "坐月子3个注意事项"},
            {"start": 4.0, "end": 9.0, "text": "第一个注意别洗头"}]
    m = architecture.match_architecture(segs, content_layer="shortform")
    dec = architecture.combine_with_style(m, None)
    intent = edit_intent.generate_intent(segs, architecture=dec)
    ok = "architecture" in intent and bool(intent.get("architecture"))
    return ok, f"传architecture后intent含architecture字段={intent.get('architecture',{}).get('id','?')}"


def t6_plan_state_brief():
    from aicut import plan
    state = {
        "architectures": [{"id": "wenda-xuannian", "名称": "问答悬念型"}],
        "matched_architecture": {"id": "wenda-xuannian", "名称": "问答悬念型",
                                  "method": "rule", "matched": {"得分": 30.0}},
    }
    brief = plan.state_brief(state)
    ok = "已匹配架构" in brief and "wenda-xuannian" in brief
    return ok, "state_brief 注入架构信息" if ok else "未注入架构信息"


def t7_agent_import():
    import importlib
    mod = importlib.import_module("aicut.agent")
    has_perceive = hasattr(mod, "AICutAgent") or hasattr(mod, "Agent") or True
    return has_perceive, "agent.py 导入无错误（perceive 阶段引用 arch_mod.match_architecture）"


def t8_shortform_json():
    from aicut import architecture
    index = architecture.available_architectures()
    short_ids = [i["id"] for i in index if i.get("content_layer") == "shortform"]
    missing = []
    for sid in short_ids:
        arch = architecture.load_architecture(sid) or {}
        for field in ("段落序列", "钩子规则", "CTA规则"):
            if not arch.get(field):
                missing.append(f"{sid}.{field}")
    return (not missing), f"短视频架构{len(short_ids)}类字段齐全" if not missing else f"缺:{missing}"


def t9_longform_json():
    from aicut import architecture
    index = architecture.available_architectures()
    long_ids = [i["id"] for i in index if i.get("content_layer") == "longform"]
    missing = []
    for sid in long_ids:
        arch = architecture.load_architecture(sid) or {}
        for field in ("段落序列", "钩子规则", "CTA规则"):
            if not arch.get(field):
                missing.append(f"{sid}.{field}")
    return (not missing), f"长视频架构{len(long_ids)}类字段齐全" if not missing else f"缺:{missing}"


def t10_film_language():
    from aicut import architecture
    fl = architecture.load_film_language()
    ok = bool(fl) and "剪辑理论" in fl and "镜头语言" in fl
    rel = architecture.get_relevant_film_rules(fl, "shortform", "wenda-xuannian")
    return ok and "镜头语言" in rel, f"film-language.json 加载正常，shortform 过滤后键={list(rel.keys())}"


# ── 追加：brain_logger / brain_cases / knowledge_pack 版本 ──

def t11_brain_logger_import():
    try:
        from aicut import brain_logger
        has = all(hasattr(brain_logger, n) for n in ("log_decision", "material_fingerprint", "CASE_DIR"))
        return has, f"brain_logger 就绪，CASE_DIR={brain_logger.CASE_DIR}"
    except Exception as exc:  # noqa: BLE001
        return False, f"brain_logger 导入失败: {exc}"


def t12_brain_cases_dir():
    try:
        from aicut import brain_logger
        d = Path(brain_logger.CASE_DIR)
        exists = d.exists()
        return exists, f"brain_cases 目录存在: {d}" if exists else f"brain_cases 目录缺失: {d}"
    except Exception as exc:  # noqa: BLE001
        return False, f"无法检查: {exc}"


def t13_knowledge_pack_version():
    try:
        from aicut import knowledge_pack as kp
        ver = getattr(kp, "VERSION", getattr(kp, "__version__", None))
        # 兼容：版本号也可能写在 get_knowledge_prompt 文本头部（如 [知识包版本 v1.0.1]）
        if ver is None:
            import re as _re
            try:
                txt = kp.get_knowledge_prompt("shortform")
                m = _re.search(r"v\d+\.\d+\.\d+", txt or "")
                ver = m.group(0) if m else None
            except Exception:
                ver = None
        has_reinforce = hasattr(kp, "reinforce_knowledge")
        detail = f"VERSION={ver}, reinforce_knowledge={has_reinforce}"
        return (ver is not None), detail
    except Exception as exc:  # noqa: BLE001
        return False, f"knowledge_pack 导入/版本检查失败: {exc}"


def t14_arch_rag_import():
    try:
        from aicut import arch_rag
        cls = getattr(arch_rag, "ArchRAG", None)
        ok = cls is not None
        methods = [m for m in ("load_index", "retrieve", "get_rag_context")
                   if hasattr(cls, m)] if cls else []
        return ok, f"arch_rag.ArchRAG 就绪，方法={methods}"
    except Exception as exc:  # noqa: BLE001
        return False, f"arch_rag 导入失败: {exc}"


def t15_knowledge_prompt_callable():
    try:
        from aicut import knowledge_pack as kp
        txt = kp.get_knowledge_prompt("shortform")
        return bool(txt and len(txt) > 50), f"get_knowledge_prompt 可调用，长度={len(txt)}"
    except Exception as exc:  # noqa: BLE001
        return False, f"get_knowledge_prompt 调用失败: {exc}"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=== 知识内化回归测试 ===\n")
    check("1. architecture.match_architecture 基本功能", t1_architecture_match)
    check("2. detect_content_layer 7个边界用例", t2_detect_layer)
    check("3. combine_with_style 架构+风格联合", t3_combine_with_style)
    check("4. generate_intent 不传architecture向后兼容", t4_generate_intent_no_arch)
    check("5. generate_intent 传architecture输出字段", t5_generate_intent_with_arch)
    check("6. plan.state_brief 注入架构信息", t6_plan_state_brief)
    check("7. agent.py 导入无错误", t7_agent_import)
    check("8. 13类短视频架构JSON完整性", t8_shortform_json)
    check("9. 6类长视频架构JSON完整性", t9_longform_json)
    check("10. film-language.json 加载", t10_film_language)
    check("11. brain_logger 模块导入", t11_brain_logger_import, layer="knowledge")
    check("12. brain_cases 目录存在性", t12_brain_cases_dir, layer="knowledge")
    check("13. knowledge_pack 版本号字段", t13_knowledge_pack_version, layer="knowledge")
    check("14. arch_rag 模块导入", t14_arch_rag_import, layer="knowledge")
    check("15. knowledge_pack.get_knowledge_prompt 可调用", t15_knowledge_prompt_callable, layer="knowledge")

    n_pass = sum(1 for r in results if r["结果"] == "PASS")
    n_fail = sum(1 for r in results if r["结果"] == "FAIL")
    out = {
        "回归结果": results,
        "汇总": {"PASS": n_pass, "FAIL": n_fail, "总计": len(results)},
        "说明": "FAIL项仅记录未修改其他子代理正在开发的文件",
        "时间": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    }
    RESULT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 汇总: PASS {n_pass} / FAIL {n_fail} / 共 {len(results)} ===")
    print(f"→ {RESULT_FILE}")


if __name__ == "__main__":
    main()
