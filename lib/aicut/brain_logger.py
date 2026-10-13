# -*- coding: utf-8 -*-
r"""剪辑大脑决策日志与失败案例库（brain_logger）——持续进化闭环的数据底座。

两类留档：
  1. 决策日志 brain_logs/：每次架构决策全量留档（含规则 vs 模型差异、用户是否采纳）；
  2. 失败案例 brain_cases/：模型与规则不一致 / 用户改稿时沉淀案例，
     供 arch_rag 优先召回，形成「犯错→补库→下次少犯」的闭环。

目录（基于 paths.DATA = 08-data）：
  08-data/brain_logs/brain_log_{时间戳}_{指纹前8}.json
  08-data/brain_cases/case_{时间戳}_{指纹前8}.json
  08-data/brain_cases/cases_index.json

用法：
    from aicut import brain_logger
    fp = brain_logger.material_fingerprint(segments)
    brain_logger.log_decision(fp, matched_arch="wenda-xuannian",
                              architecture_decision=ad,
                              rule_vs_model_diff={"rule": "koubo", "model": "wenda"},
                              user_adopted=True)
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .paths import DATA

LOG_DIR = DATA / "brain_logs"
CASE_DIR = DATA / "brain_cases"
CASES_INDEX = CASE_DIR / "cases_index.json"

MODEL_VERSION = "knowledge-internalization-v1.0"


def material_fingerprint(segments: list[dict], meta: dict | None = None) -> str:
    """素材指纹：时长 + 全文文本的 MD5 前 16 位，用于去重与留档。"""
    total = sum(float(s.get("end", 0)) - float(s.get("start", 0)) for s in (segments or []))
    text = "".join(str(s.get("text", "")) for s in (segments or []))
    raw = f"{round(total,2)}|{len(segments or [])}|{text[:500]}"
    if meta:
        raw += "|" + str(meta.get("task", ""))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _kp_version() -> str:
    """读取知识包当前版本（供日志追溯，失败返回 unknown）。"""
    try:
        from . import knowledge_pack
        return getattr(knowledge_pack, "KNOWLEDGE_PACK_VERSION", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def log_decision(material_fingerprint: str, matched_arch: str,
                 architecture_decision: dict | None = None,
                 rule_vs_model_diff: dict | None = None,
                 user_adopted: bool | None = None,
                 content_layer: str = "shortform") -> Path:
    """写入一条架构决策日志。返回落盘路径。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    rec: dict[str, Any] = {
        "时间戳": _now(),
        "素材指纹": material_fingerprint,
        "matched_arch": matched_arch,
        "content_layer": content_layer,
        "architecture_decision": architecture_decision or {},
        "规则vs模型差异": rule_vs_model_diff or {},
        "用户是否采纳": user_adopted,   # None=未反馈 / True=采纳 / False=修改
        "模型版本": MODEL_VERSION,
        "knowledge_pack版本": _kp_version(),
    }
    fp = LOG_DIR / f"brain_log_{ts}_{material_fingerprint[:8]}.json"
    fp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def _load_cases_index() -> dict:
    if CASES_INDEX.exists():
        try:
            return json.loads(CASES_INDEX.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            pass
    return {"cases": [], "updated_at": ""}


def _save_cases_index(idx: dict) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    idx["updated_at"] = _now()
    CASES_INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def log_case(material_fingerprint: str, transcript_snippet: str,
             model_decision: dict, rule_decision: dict,
             discrepancy_type: str = "架构误判",
             resolution: str | None = None,
             keywords: list[str] | None = None) -> Path:
    """沉淀一条失败/差异案例，并更新 cases_index.json 供 RAG 召回。

    discrepancy_type 取值：架构误判 / 段落缺失 / 剪辑语言错误 / 用户改稿。
    """
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    rec = {
        "时间戳": _now(),
        "素材指纹": material_fingerprint,
        "转写片段": transcript_snippet[:500],
        "模型决策": model_decision,
        "规则决策": rule_decision,
        "差异类型": discrepancy_type,
        "最终解决方案": resolution or "",
        "关键词": keywords or [],
    }
    fp = CASE_DIR / f"case_{ts}_{material_fingerprint[:8]}.json"
    fp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    idx = _load_cases_index()
    idx["cases"].append({
        "file": fp.name,
        "素材指纹": material_fingerprint,
        "转写片段": transcript_snippet[:200],
        "model_arch": (model_decision or {}).get("matched_arch", ""),
        "rule_arch": (rule_decision or {}).get("matched_arch", ""),
        "差异类型": discrepancy_type,
        "关键词": keywords or [],
        "时间戳": _now(),
    })
    _save_cases_index(idx)
    return fp


def load_case_blocks() -> list[dict]:
    """把历史案例读成可被 arch_rag 检索的段落块。"""
    blocks: list[dict] = []
    idx = _load_cases_index()
    for c in idx.get("cases", []):
        fp = CASE_DIR / c["file"]
        if not fp.exists():
            continue
        try:
            rec = json.loads(fp.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            continue
        text = (f"【历史案例】{rec.get('差异类型','')}："
                f"素材「{rec.get('转写片段','')[:80]}」；"
                f"模型判为 {rec.get('模型决策',{}).get('matched_arch','?')}，"
                f"规则判为 {rec.get('规则决策',{}).get('matched_arch','?')}；"
                f"经验：{rec.get('最终解决方案','') or '复盘此差异'}。")
        blocks.append({
            "arch_id": "brain-case",
            "arch_name": "历史失败案例",
            "content_layer": "case",
            "field": f"历史案例-{rec.get('差异类型','')}",
            "text": text,
            "keywords": rec.get("关键词", []) + c.get("关键词", []),
        })
    return blocks


def detect_and_log_case(material_fingerprint: str, transcript_snippet: str,
                        model_arch: str, rule_arch: str,
                        model_decision: dict | None = None,
                        rule_decision: dict | None = None) -> Path | None:
    """规则 vs 模型不一致时自动沉淀案例。返回案例路径；一致时返回 None。"""
    if model_arch and rule_arch and model_arch != rule_arch:
        return log_case(
            material_fingerprint=material_fingerprint,
            transcript_snippet=transcript_snippet,
            model_decision=model_decision or {"matched_arch": model_arch},
            rule_decision=rule_decision or {"matched_arch": rule_arch},
            discrepancy_type="架构误判",
            keywords=[model_arch, rule_arch],
        )
    return None


if __name__ == "__main__":
    import sys
    sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    fp = material_fingerprint([{"start": 0, "end": 2, "text": "在家坐月子和月子中心三问测一测"}])
    lp = log_decision(fp, "wenda-xuannian", {"matched_arch": "wenda-xuannian"},
                      {"rule": "koubo-zhishi-arch", "model": "wenda-xuannian"})
    print("决策日志:", lp)
    cp = detect_and_log_case(fp, "三问测一测", "wenda-xuannian", "koubo-zhishi-arch")
    print("案例:", cp)
    print("案例块数:", len(load_case_blocks()))
