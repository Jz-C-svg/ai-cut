# -*- coding: utf-8 -*-
"""
模型大脑闭卷测试框架（AI cut 知识内化 · 任务2）

目标：验证本地 LLM 是否真正"学会"了短视频内容架构技能。
方法：给模型一段转写（不含架构标签），让它回答架构识别/段落规划/剪辑语言/决策理由，
     再用 architecture.match_architecture 的规则结果作为"标准答案"做对比评分。

测试模型：
  - qwen3.5:9b（主测）
  - gemma4:12b（对比，可选）
评分维度（每项 0-10）：
  1. 架构识别准确率
  2. 段落规划完整度
  3. 剪辑语言专业度
  4. 决策理由质量
  5. 与架构档案一致性

注意：knowledge_pack.py 尚未就绪，本脚本用"基础 prompt"（你是专业剪辑师）。
     待 knowledge_pack 就绪后，可在 PROMPT_MODE 加 "knowledge_injected" 版做对比。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import architecture as arch_mod  # noqa: E402

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OUT_DIR = ROOT / "08-data" / "knowledge_test"
RESULT_FILE = OUT_DIR / "model_brain_test_results.json"
WEAK_POINTS_FILE = ROOT / "data" / "knowledge_test" / "weak_points.json"

# brain_logger（并行开发中，可能未就绪；就绪则调用 log_decision，未就绪则降级记录）
try:
    from aicut import brain_logger as _brain_logger  # type: ignore
except Exception:  # noqa: BLE001
    _brain_logger = None

# 测试模型
PRIMARY_MODEL = "qwen3.5:9b"
COMPARE_MODEL = "gemma4:12b"

# 专业术语词表（用于自动评分）
SHOT_TERMS = ["特写", "大特写", "近景", "中景", "全景", "远景", "微距", "广角"]
TRANSITION_TERMS = ["硬切", "闪白", "叠化", "淡入淡出", "跳切", "J-cut", "L-cut", "匹配剪辑"]
RHYTHM_TERMS = ["快切", "节奏", "慢镜头", "卡点", "骤停", "渐快", "渐慢"]
SUBTITLE_TERMS = ["字幕", "大字", "花字", "高亮", "标题"]
HOOK_KEYS = ["钩子", "前3秒", "开场", "开头", "3秒"]
CTA_KEYS = ["CTA", "结尾", "引导", "关注", "评论", "收藏", "点赞"]


# ───────────────────────── 测试用例构建 ─────────────────────────

def load_words(path: Path) -> list[dict]:
    """加载 *_words.json / *.words.json，返回 segments[{start,end,text}]。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    segs = data.get("segments") or []
    out = []
    for s in segs:
        t = str(s.get("text", "")).strip()
        if not t:
            continue
        out.append({"start": float(s.get("start", 0.0)), "end": float(s.get("end", 0.0)), "text": t})
    return out


def build_cases() -> list[dict]:
    """从真实转写数据构建闭卷测试用例。"""
    tr = ROOT / "02-transcript"
    cases: list[dict] = []

    # 用例1：合并 2026.9.12 三问测一测系列（IMG_5854-5861）为一条完整口播
    series = ["IMG_5854.words.json", "IMG_5855.words.json", "IMG_5856.words.json",
              "IMG_5857.words.json", "IMG_5858.words.json", "IMG_5859.words.json",
              "IMG_5860.words.json", "IMG_5861.words.json"]
    merged: list[dict] = []
    for fn in series:
        p = tr / fn
        if p.exists():
            merged.extend(load_words(p))
    if merged:
        cases.append({"id": "三问测一测(合并)", "来源": "+".join(series), "segments": merged})

    # 用例2/3：单文件
    for fn, cid in [("IMG_5840_words.json", "黄疸口播"),
                    ("yuezi_0299_words.json", "选月子中心"),
                    ("IMG_5855.words.json", "三问-第一问"),
                    ("IMG_5859.words.json", "三问-第三问(简体)")]:
        p = tr / fn
        if p.exists():
            segs = load_words(p)
            if len(segs) >= 2:
                cases.append({"id": cid, "来源": fn, "segments": segs})
    return cases


# ───────────────────────── LLM 调用 ─────────────────────────

def call_ollama(model: str, prompt: str, timeout: int = 240) -> dict:
    """调用本地 Ollama，返回 {ok, text, elapsed, error}。
    注意：qwen3.5 为推理模型，think:False 让答案直接进 response；否则思考占满 token。"""
    t0 = time.time()
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": model, "prompt": prompt, "stream": False, "think": False,
            "options": {"temperature": 0.3, "num_predict": 1400},
        }, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return {"ok": True, "text": data.get("response", ""),
                "elapsed": round(time.time() - t0, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": "", "elapsed": round(time.time() - t0, 1),
                "error": f"{type(exc).__name__}: {exc}"}


def build_prompt(case: dict, layer: str, knowledge_ctx: str = "") -> str:
    """构造闭卷测试 prompt（不告诉模型标准答案）。
    knowledge_ctx 非空时为'知识注入版'，前置 knowledge_prompt。"""
    segs = case["segments"]
    total = round(sum(s["end"] - s["start"] for s in segs), 1)
    transcript_text = "".join(s["text"] for s in segs)
    head = knowledge_ctx + "\n\n" if knowledge_ctx else (
        "你是一名专业短视频剪辑师。\n")
    return f"""{head}现在审看一段素材的口播转写。
素材总时长约 {total} 秒，内容层级：{layer}。
口播转写全文：
「{transcript_text}」

请严格按以下4个问题回答，用简洁中文：
a) 这是什么内容架构？（从常见短视频/长视频架构里选一个最合适的，给出架构名和一句话判断）
b) 段落怎么排？请列出你建议的段落规划（钩子/主体/CTA 或对应段落功能），并给每段大致时长占比。
c) 该用什么剪辑语言？请给出景别、转场、节奏、字幕方面的具体建议。
d) 你的决策理由是什么？请基于上面这段口播的文本特征（如问句、对比、步骤、情绪等）说明你为什么这么选，不要泛泛而谈。

直接回答，不要复述问题。"""


# ───────────────────────── 自动评分 ─────────────────────────

def extract_arch_id(text: str) -> str | None:
    """从模型回答里识别它选了哪个架构 id。
    取文本中【最先出现】的架构 id/名称（模型在 a) 先给结论，后文可能拿别的架构做对比）。"""
    index = arch_mod.available_architectures()
    # 收集所有命中位置，取最靠前的
    hits: list[tuple[int, str]] = []
    for item in index:
        aid = item["id"]
        p = text.find(aid)
        if p >= 0:
            hits.append((p, aid))
        else:
            name = item.get("名称", "")
            if name:
                pn = text.find(name)
                if pn >= 0:
                    hits.append((pn, aid))
    if not hits:
        return None
    hits.sort()
    return hits[0][1]


def score_arch_identify(predicted: str | None, gold_id: str) -> tuple[float, str]:
    """维度1：架构识别准确率。"""
    if not predicted:
        return 0.0, "模型未明确指出架构"
    if predicted == gold_id:
        return 10.0, f"识别一致：{predicted}"
    # 同层级视为部分分
    pred_arch = arch_mod.load_architecture(predicted)
    gold_arch = arch_mod.load_architecture(gold_id)
    if pred_arch and gold_arch:
        if pred_arch.get("content_layer") == gold_arch.get("content_layer"):
            return 5.0, f"层级一致但架构不同：{predicted} vs {gold_id}"
    return 1.0, f"识别错误：{predicted} vs 标准答案 {gold_id}"


def score_paragraph_plan(text: str, gold_arch: dict) -> tuple[float, str]:
    """维度2：段落规划完整度（钩子/主体/CTA 覆盖 + 段落数）。"""
    hits = sum(1 for k in HOOK_KEYS if k in text)
    cta_hits = sum(1 for k in CTA_KEYS if k in text)
    seg_functions = [s.get("功能", "") for s in (gold_arch.get("段落序列") or [])]
    # 模型是否提到多个段落（用"第/段/%"等信号）
    n_paras = len(re.findall(r"(第[一二三四五六七八]|[0-9]+\.?\s*[:：]|段)", text))
    score = 0.0
    if hits:
        score += 3.5
    if cta_hits:
        score += 3.5
    if "%" in text or "占比" in text or "秒" in text:
        score += 1.5
    if n_paras >= 2:
        score += 1.5
    score = min(10.0, score)
    detail = f"钩子命中{hits}处/CTA命中{cta_hits}处/段落信号{n_paras}处"
    return score, detail


def score_film_language(text: str) -> tuple[float, str]:
    """维度3：剪辑语言专业度。"""
    n_shot = sum(1 for t in SHOT_TERMS if t in text)
    n_trans = sum(1 for t in TRANSITION_TERMS if t in text)
    n_rhy = sum(1 for t in RHYTHM_TERMS if t in text)
    n_sub = sum(1 for t in SUBTITLE_TERMS if t in text)
    # 景别满分3，转场3，节奏2，字幕2
    score = min(3, n_shot) + min(3, n_trans) + min(2, n_rhy) + min(2, n_sub)
    detail = f"景别{n_shot}/转场{n_trans}/节奏{n_rhy}/字幕{n_sub}"
    return float(score), detail


def score_reasoning(text: str) -> tuple[float, str]:
    """维度4：决策理由质量（是否基于素材特征推理）。"""
    evidence_terms = ["因为", "由于", "根据", "素材", "文本", "问句", "对比", "步骤",
                      "情绪", "口播", "转写", "开头", "前3秒"]
    hits = sum(1 for t in evidence_terms if t in text)
    score = min(10.0, hits * 2.0)
    return score, f"理由证据词命中{hits}处"


def score_arch_consistency(text: str, gold_arch: dict) -> tuple[float, str]:
    """维度5：与架构档案一致性（模型提到的段落功能/钩子/CTA是否与档案吻合）。"""
    seg_functions = [s.get("功能", "") for s in (gold_arch.get("段落序列") or [])]
    matched = [f for f in seg_functions if f and f in text]
    ratio = len(matched) / max(1, len(seg_functions))
    score = round(ratio * 10.0, 1)
    detail = f"段落功能命中 {len(matched)}/{len(seg_functions)}：{matched}"
    return score, detail


# ───────────────────────── 主流程 ─────────────────────────

def run_case(case: dict, model: str, prompt_mode: str = "base", knowledge_ctx: str = "") -> dict:
    segs = case["segments"]
    layer = arch_mod.detect_content_layer(segs)
    gold = arch_mod.match_architecture(segs, content_layer=layer)
    gold_id = gold["matched"]["id"]
    gold_arch = arch_mod.load_architecture(gold_id) or {}

    prompt = build_prompt(case, layer, knowledge_ctx)
    llm = call_ollama(model, prompt)

    record = {
        "用例": case["id"],
        "来源": case["来源"],
        "模型": model,
        "prompt版本": prompt_mode,
        "标准答案": {
            "架构": gold_id,
            "架构名称": gold["matched"].get("名称", ""),
            "匹配方法": gold.get("method", ""),
            "内容层级": layer,
            "段落功能": [s.get("功能", "") for s in gold_arch.get("段落序列", [])],
        },
        "模型回答": llm.get("text", "") if llm["ok"] else f"[调用失败] {llm.get('error','')}",
        "耗时秒": llm.get("elapsed"),
    }
    if llm["ok"]:
        ans = llm["text"]
        predicted = extract_arch_id(ans)
        a1, d1 = score_arch_identify(predicted, gold_id)
        a2, d2 = score_paragraph_plan(ans, gold_arch)
        a3, d3 = score_film_language(ans)
        a4, d4 = score_reasoning(ans)
        a5, d5 = score_arch_consistency(ans, gold_arch)
        record["评分"] = {
            "1_架构识别准确率": {"分": a1, "说明": d1},
            "2_段落规划完整度": {"分": a2, "说明": d2},
            "3_剪辑语言专业度": {"分": a3, "说明": d3},
            "4_决策理由质量": {"分": a4, "说明": d4},
            "5_与架构档案一致性": {"分": a5, "说明": d5},
            "总分": round((a1 + a2 + a3 + a4 + a5) / 5, 1),
        }
        record["模型识别架构"] = predicted
        # ── 决策日志：brain_logger.log_decision（未就绪则降级记录）──
        rule_vs_model_diff = {
            "标准答案架构": gold_id,
            "模型识别架构": predicted,
            "是否一致": predicted == gold_id,
            "差异说明": d1,
        }
        try:
            if _brain_logger is not None and hasattr(_brain_logger, "log_decision"):
                fp = _brain_logger.material_fingerprint(case["来源"]) if hasattr(
                    _brain_logger, "material_fingerprint") else f"{model}:{case['id']}"
                log_path = _brain_logger.log_decision(
                    material_fingerprint=fp,
                    matched_arch=predicted or gold_id,
                    architecture_decision={"段落功能": record["评分"]["1_架构识别准确率"]["说明"]},
                    rule_vs_model_diff=rule_vs_model_diff,
                    user_adopted=None,
                    content_layer=layer,
                )
                record["决策日志"] = {"已写入": True, "路径": str(log_path)}
            else:
                record["决策日志"] = {"已写入": False, "原因": "brain_logger 未就绪，字段内联记录",
                                      "指纹": f"{model}:{case['id']}", "rule_vs_model_diff": rule_vs_model_diff}
        except Exception as exc:  # noqa: BLE001
            record["决策日志"] = {"已写入": False, "原因": f"log_decision 调用失败: {exc}",
                                  "rule_vs_model_diff": rule_vs_model_diff}
    else:
        record["评分"] = {"总分": 0.0, "错误": llm.get("error")}
    return record


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    print(f"[信息] 构建 {len(cases)} 个闭卷测试用例")
    models = [PRIMARY_MODEL]
    # gemma4 对比模型：若可用则加入
    try:
        tags = requests.get("http://127.0.0.1:11434/api/tags", timeout=10).json()
        names = [m["name"] for m in tags.get("models", [])]
        if COMPARE_MODEL in names:
            models.append(COMPARE_MODEL)
    except Exception:
        pass

    # knowledge_pack 知识注入（并行开发已就绪）
    kp_ctx = ""
    kp_version = "未加载"
    try:
        from aicut import knowledge_pack as kp  # type: ignore
        kp_ctx = kp.get_knowledge_prompt("shortform")
        m = __import__("re").search(r"v\d+\.\d+\.\d+", kp_ctx)
        kp_version = m.group(0) if m else "loaded"
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] knowledge_pack 未加载: {exc}")

    results = []
    # 主测模型跑 base + knowledge 两版；对比模型只跑 base
    plan = [(PRIMARY_MODEL, "base"), (PRIMARY_MODEL, "knowledge")]
    if COMPARE_MODEL in [m["name"] for m in requests.get(
            "http://127.0.0.1:11434/api/tags", timeout=10).json().get("models", [])]:
        plan.append((COMPARE_MODEL, "base"))
    for model, mode in plan:
        ctx = kp_ctx if mode == "knowledge" else ""
        print(f"[信息] 测试模型 {model}（{mode}版）...")
        for case in cases:
            print(f"  - 用例 {case['id']}")
            rec = run_case(case, model, prompt_mode=mode, knowledge_ctx=ctx)
            results.append(rec)
            sc = rec.get("评分", {}).get("总分", 0)
            print(f"    总分 {sc}")

    # 汇总各（模型 × prompt版本）平均分
    summary: dict = {}
    dim_keys = ["1_架构识别准确率", "2_段落规划完整度", "3_剪辑语言专业度",
                "4_决策理由质量", "5_与架构档案一致性"]
    groups = sorted(set((r["模型"], r.get("prompt版本", "base")) for r in results))
    for model, mode in groups:
        rs = [r for r in results if r["模型"] == model and r.get("prompt版本", "base") == mode]
        if not rs:
            continue
        dim_avg = {}
        for k in dim_keys:
            vals = [r["评分"][k]["分"] for r in rs if k in r.get("评分", {})]
            dim_avg[k] = round(sum(vals) / len(vals), 1) if vals else 0
        totals = [r["评分"]["总分"] for r in rs]
        summary[f"{model}/{mode}"] = {
            "模型": model, "prompt版本": mode,
            "用例数": len(rs),
            "各维度平均分": dim_avg,
            "总平均分": round(sum(totals) / len(totals), 1) if totals else 0,
        }

    out = {
        "测试设计": {
            "说明": "闭卷：给转写文本+4问，模型不可见标准答案；用 match_architecture 规则结果做金标准",
            "主测模型": PRIMARY_MODEL,
            "对比模型": COMPARE_MODEL,
            "评分维度": ["架构识别准确率", "段落规划完整度", "剪辑语言专业度",
                          "决策理由质量", "与架构档案一致性"],
            "prompt版本": "base（专业剪辑师）vs knowledge（knowledge_pack 注入 v" + kp_version + "）",
        },
        "用例数": len(cases),
        "模型汇总": summary,
        "明细": results,
        "测试时间": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    RESULT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] 结果 → {RESULT_FILE}")
    for k, s in summary.items():
        print(f"  {k}: 总平均 {s['总平均分']}")

    # ── 薄弱知识点清单：维度 < 6 分（以知识注入版主测模型为主，base 也纳入）──
    dim_label = {
        "1_架构识别准确率": "架构识别",
        "2_段落规划完整度": "段落规划",
        "3_剪辑语言专业度": "剪辑语言",
        "4_决策理由质量": "决策理由",
        "5_与架构档案一致性": "钩子设计",
    }
    weak_points = build_weak_points(results, dim_label)
    WEAK_POINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WEAK_POINTS_FILE.write_text(
        json.dumps(weak_points, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] 薄弱知识点清单（{len(weak_points)} 条）→ {WEAK_POINTS_FILE}")

    # ── 测试→补强闭环：回灌 knowledge_pack.reinforce_knowledge ──
    try:
        from aicut import knowledge_pack as kp  # type: ignore
        if weak_points and hasattr(kp, "reinforce_knowledge"):
            res = kp.reinforce_knowledge(weak_points, "闭卷测试反馈回灌(base vs knowledge版)")
            out["补强回灌"] = {"已调用": True, "结果": str(res)[:300]}
            print(f"[完成] 已回灌 reinforce_knowledge（{len(weak_points)} 条薄弱点）")
        else:
            out["补强回灌"] = {"已调用": False, "原因": "无薄弱点或 reinforce_knowledge 未就绪"}
    except Exception as exc:  # noqa: BLE001
        out["补强回灌"] = {"已调用": False, "原因": f"{exc}"}


def build_weak_points(results: list[dict], dim_label: dict) -> list[dict]:
    """从测试明细抽取维度分<6 的薄弱知识点，供 knowledge_pack.reinforce_knowledge() 调用。"""
    weak: list[dict] = []
    for rec in results:
        sc = rec.get("评分", {})
        gold_arch = rec.get("标准答案", {})
        gold_id = gold_arch.get("架构", "")
        funcs = gold_arch.get("段落功能", [])
        arch = arch_mod.load_architecture(gold_id) or {}
        hook = arch.get("钩子规则") or {}
        cta = arch.get("CTA规则") or {}
        for dim_key, label in dim_label.items():
            cell = sc.get(dim_key)
            if not isinstance(cell, dict):
                continue
            score = cell.get("分", 10)
            if score < 6:
                # 补强建议：指向该架构档案的真实规则
                if label == "架构识别":
                    sug = (f"强化 {gold_id}({arch.get('名称','')}) 的识别边界："
                           f"{(arch.get('匹配规则') or {}).get('匹配关键词', [])}；"
                           f"常见误判对照见微调困难案例 difficulty=hard")
                elif label == "段落规划":
                    sug = (f"按 {gold_id} 段落序列排：{funcs}；"
                           f"钩子/主体/CTA 三段不可缺，时长占比参考档案")
                elif label == "剪辑语言":
                    sug = (f"为 {gold_id} 配景别/转场/节奏：参考 film-language.json 镜头语言 + "
                           f"架构转场建议 {arch.get('转场建议', {})}")
                elif label == "钩子设计":
                    sug = (f"钩子话术母版：{hook.get('话术模式', [])}；"
                           f"CTA话术：{cta.get('话术模式', [])}；视觉配合：{hook.get('视觉配合','')}")
                else:
                    sug = f"要求模型基于素材特征（问句/对比/步骤/情绪）推理，而非泛泛而谈"
                weak.append({
                    "arch_id": gold_id,
                    "weak_dimension": label,
                    "issue": f"用例「{rec.get('用例')}」{label}得分{score}/10：{cell.get('说明','')}",
                    "test_score": score,
                    "supplement_suggestion": sug,
                })
    return weak


if __name__ == "__main__":
    main()
