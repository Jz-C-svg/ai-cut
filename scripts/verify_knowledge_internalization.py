# -*- coding: utf-8 -*-
r"""知识内化阶段验证脚本：knowledge_pack + arch_rag + edit_intent architecture_decision 全链路。

运行（项目根 <project-root>）：
    venv\Scripts\python.exe -X utf8 scripts\verify_knowledge_internalization.py

验证项：
  1. knowledge_pack.get_knowledge_prompt() 非空且含架构知识
  2. arch_rag.retrieve() 能召回相关架构段落
  3. edit_intent.generate_intent() 输出强制包含 architecture_decision 块
  4. 用 02-transcript 真实素材跑通「知识注入→架构决策→方案输出」全链路（2 个素材）
  5. 结果落盘 08-data\knowledge_test\
"""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import json
import sys
import time
from pathlib import Path

ROOT = Path(portable_path('project:'))
sys.path.insert(0, str(ROOT / "lib"))

from aicut import knowledge_pack, arch_rag, edit_intent, architecture  # noqa: E402
from aicut import brain_logger  # noqa: E402

OUT_DIR = ROOT / "08-data" / "knowledge_test"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PASS, FAIL = "PASS", "FAIL"
results: list[dict] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    results.append({"test": name, "result": PASS if ok else FAIL, "detail": detail[:200]})
    print(f"  [{PASS if ok else FAIL}] {name}  {detail[:120]}")


# ───────────────────── 单元测试 ─────────────────────

print("=" * 60)
print("测试1：knowledge_pack 知识包")
kp_full = knowledge_pack.get_knowledge_prompt()
kp_locked = knowledge_pack.get_knowledge_prompt("shortform", "wenda-xuannian")
report("知识包全量非空", len(kp_full) > 2000, f"{len(kp_full)}字")
report("锁定架构裁剪含该架构", "问答悬念型" in kp_locked and "钩子规则" in kp_locked,
       f"{len(kp_locked)}字")
report("含电影语言决策规则", "剪辑点" in kp_locked and "景别" in kp_locked)
report("含决策方法论", "四步" in kp_locked or "决策方法论" in kp_locked)

print("测试2：arch_rag 轻量检索")
rag = arch_rag.ArchRAG().load_index()
report("RAG 索引建立", len(rag._blocks) > 100, f"{len(rag._blocks)}块")
hits = rag.retrieve("在家坐月子和月子中心三问测一测你知道吗", content_layer="shortform", top_k=3)
top_ids = [h["arch_id"] for h in hits]
report("三问测一测召回问答/口播架构",
       any(i in ("wenda-xuannian", "koubo-zhishi-arch") for i in top_ids),
       " -> ".join(top_ids))
seg = rag.retrieve_segment("koubo-zhishi-arch", "钩子 痛点")
report("架构内段落检索", len(seg) >= 1 and seg[0]["arch_id"] == "koubo-zhishi-arch",
       seg[0]["field"] if seg else "空")
ctx = rag.get_rag_context("钩子怎么设计", content_layer="shortform")
report("RAG 上下文文本", "RAG 召回" in ctx, f"{len(ctx)}字")

# ───────────────────── 进化闭环测试 ─────────────────────

print("测试3：brain_logger 决策日志 + 失败案例库")
_seg_demo = [{"start": 0, "end": 2, "text": "在家坐月子和月子中心三问测一测"},
             {"start": 2, "end": 5, "text": "第一个问题家里有人帮你带孩子吗"}]
_fp = brain_logger.material_fingerprint(_seg_demo, {"task": "进化闭环验证"})
_lp = brain_logger.log_decision(_fp, "wenda-xuannian", {"matched_arch": "wenda-xuannian"},
                                {"rule": "koubo", "model": "wenda"}, user_adopted=True)
report("决策日志写入", _lp.exists(), str(_lp.name))
_cp = brain_logger.detect_and_log_case(_fp, "三问测一测 在家做月子",
                                       "wenda-xuannian", "koubo-zhishi-arch")
report("规则vs模型差异自动沉淀案例", _cp is not None and _cp.exists(), str(_cp.name))
_idx = (ROOT / "08-data" / "brain_cases" / "cases_index.json")
report("cases_index.json 更新", _idx.exists(), f"{len(__import__('json').loads(_idx.read_text(encoding='utf-8'))['cases'])}条")

# 刷新索引后案例应被优先召回
arch_rag.refresh_rag()
_hits = arch_rag.get_rag_context("在家做月子 三问测一测 在家坐月子", content_layer="shortform")
report("历史案例被RAG召回并标注", "历史案例" in _hits, _hits[:120])

print("测试4：版本化检查")
_kp_json = ROOT / "content-library" / "knowledge_pack.json"
_kp_data = __import__("json").loads(_kp_json.read_text(encoding="utf-8"))
report("knowledge_pack.json 含 version", bool(_kp_data.get("version")),
       f"v{_kp_data.get('version')}")
report("含 updated_at/update_notes/reinforced_points",
       bool(_kp_data.get("updated_at")) and isinstance(_kp_data.get("update_notes"), list)
       and isinstance(_kp_data.get("reinforced_points"), list))
report("get_knowledge_prompt 含版本号",
       "知识包版本" in knowledge_pack.get_knowledge_prompt("shortform", "koubo-zhishi-arch"))

# ───────────────────── 全链路：真实素材 ─────────────────────

def load_segments(fp: Path) -> list[dict]:
    d = json.loads(fp.read_text(encoding="utf-8"))
    segs = d.get("segments") or []
    # words.json 的 segments 已带 start/end/text，直接可用
    out = [{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)),
            "text": str(s.get("text", "")).strip()} for s in segs if str(s.get("text", "")).strip()]
    return out


def run_material(tag: str, segments: list[dict], expected_hint: str = "") -> dict:
    print("=" * 60)
    print(f"全链路素材：{tag}（{len(segments)}句，"
          f"{round(sum(float(s['end'])-float(s['start']) for s in segments),1)}秒）")
    t0 = time.time()
    # 1. 层级判定 + 架构匹配
    layer = architecture.detect_content_layer(segments, {"task": "知识内化验证"})
    match = architecture.match_architecture(segments, semantic_doc=None, meta={"task": "知识内化验证"},
                                           content_layer=layer)
    print(f"  架构匹配：{match['matched']['名称']}({match['matched']['id']}) "
          f"方法={match['method']} 层级={layer}")
    # 2. 电影语言 + 联合决策
    fl = architecture.load_film_language()
    relevant = architecture.get_relevant_film_rules(fl, layer, match["matched"]["id"])
    decision = architecture.combine_with_style(match, None, film_language=relevant)
    # 3. 生成剪辑意图（注入知识包+RAG，强制 architecture_decision）
    try:
        intent = edit_intent.generate_intent(segments, architecture=decision, timeout=300)
        live_llm = bool(intent.get("_meta", {}).get("provider"))
    except Exception as exc:  # noqa: BLE001
        report(f"{tag} generate_intent 异常", False, str(exc))
        return {}
    ad = intent.get("architecture_decision") or {}
    # 校验 schema 完整性
    required = ["matched_arch", "content_layer", "匹配理由", "段落计划",
                "剪辑语言应用点", "钩子设计", "CTA设计"]
    missing = [k for k in required if k not in ad]
    report(f"{tag} 含 architecture_decision", not missing,
           f"缺字段={missing}" if missing else f"matched_arch={ad.get('matched_arch')}")
    report(f"{tag} 段落计划非空", len(ad.get("段落计划") or []) > 0,
           f"{len(ad.get('段落计划') or [])}段")
    report(f"{tag} 剪辑语言应用点非空", len(ad.get("剪辑语言应用点") or []) > 0)
    report(f"{tag} 逐句意图已生成", len(intent.get("逐句意图") or []) > 0,
           f"{len(intent.get('逐句意图') or [])}句")
    src = ad.get("_来源") or ad.get("_补全") or ("LLM 直出" if live_llm else "规则兜底")
    print(f"  决策来源：{src}（LLM后端={intent['_meta'].get('provider') or '无'}），耗时{time.time()-t0:.1f}s")
    # 落盘
    (OUT_DIR / f"{tag}.intent.json").write_text(
        json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8")
    sheet = edit_intent.intent_decision_sheet(intent)
    (OUT_DIR / f"{tag}.决策单.txt").write_text(sheet, encoding="utf-8")
    print(f"  已落盘：{tag}.intent.json / .决策单.txt")
    return intent


# 素材A：「在家vs月子中心 三问测一测」（合并 5854-5857 四段）
seg_a: list[dict] = []
t_off = 0.0
for n in (5854, 5855, 5856, 5857):
    fp = ROOT / "02-transcript" / f"IMG_{n}.words.json"
    for s in load_segments(fp):
        seg_a.append({"start": round(s["start"] + t_off, 2), "end": round(s["end"] + t_off, 2),
                      "text": s["text"]})
    if seg_a:
        t_off = seg_a[-1]["end"]
intent_a = run_material("素材A-三问测一测", seg_a, "wenda-xuannian")

# 素材B：「选月子中心不要被忽悠」
seg_b = load_segments(ROOT / "02-transcript" / "月子知识-0299.json")
intent_b = run_material("素材B-选月子中心避坑", seg_b, "koubo-zhishi")

# ───────────────────── 汇总 ─────────────────────
print("=" * 60)
passed = sum(1 for r in results if r["result"] == PASS)
print(f"验证汇总：{passed}/{len(results)} 通过")
summary = {
    "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "results": results,
    "pass_rate": f"{passed}/{len(results)}",
    "materials": {
        "A": {"file": "素材A-三问测一测", "arch": (intent_a.get("_meta") or {}).get("arch_id")},
        "B": {"file": "素材B-选月子中心避坑", "arch": (intent_b.get("_meta") or {}).get("arch_id")},
    },
}
(OUT_DIR / "验证汇总.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"汇总已落盘：{OUT_DIR}")
sys.exit(0 if passed == len(results) else 1)
