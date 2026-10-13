#!/usr/bin/env python3
"""Build a deduplicated, evidence-linked database for editing visual styles."""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "08-data/local-datasets/账号导出-20260923"
DEFAULT_DRAFTS = Path(portable_path('user:AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft'))
CATEGORY_MAP = {"kinetic_text": "花字", "sticker": "贴图", "filter": "滤镜", "effect": "特效"}
STYLE_LABELS = {"unclassified": "未分类原片候选", "visually-observed": "已观察静帧样式（AI观察）",
    "jianying-resource": "剪映草稿素材", "jianying-resource-registry": "剪映资源注册表",
    "jianying-history": "历史剪辑习惯", "consistency": "一致性参考", "hierarchy": "视觉层级参考",
    "placement": "版式位置参考", "progress": "逐步揭示参考", "restraint": "克制使用参考",
    "semantic": "语义贴合参考"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--drafts", type=Path, default=DEFAULT_DRAFTS)
    ap.add_argument("--out", type=Path, default=ROOT / "content-library/visual-style-db")
    args = ap.parse_args()
    out = args.out
    styles_dir = out / "styles"
    styles_dir.mkdir(parents=True, exist_ok=True)

    # Canonical evidence IDs deduplicate by exact image hash when present, else by source+locator.
    records: dict[str, dict] = {}
    category_refs: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    source_aliases: dict[str, set[str]] = defaultdict(set)

    def add(*, category: str, style: str, source_kind: str, source_id: str,
            source_path: str = "", time=None, frame_path: str = "", frame_sha256: str = "",
            label: str = "", features=None, status="automatic_unreviewed", details=None):
        key = ("frame:" + frame_sha256.lower()) if frame_sha256 else f"{source_kind}:{source_id}:{time}:{label}"
        rid = "visual-" + digest(key)
        if rid not in records:
            records[rid] = {"id": rid, "dedup_key": key, "categories": [], "style_ids": [],
                "evidence": {"kind": source_kind, "source_id": source_id, "source_path": source_path,
                    "time_seconds": time, "frame_path": frame_path or None, "frame_sha256": frame_sha256 or None},
                "label": label, "features": features or {}, "evidence_status": status,
                "rights_status": "local_only_unverified" if source_kind in {"account_video_frame", "jianying_draft"} else "reference_only_unverified",
                "production_use_allowed": False}
        row = records[rid]
        if category not in row["categories"]: row["categories"].append(category)
        if style not in row["style_ids"]: row["style_ids"].append(style)
        if details:
            row.setdefault("details", {}).update(details)
        category_refs[style][category].add(rid)
        source_aliases[source_id].add(rid)
        return rid

    # All sparse frame references are read from the existing processed collection; no re-encoding.
    kinetic_path = args.dataset / "production-visual/kinetic-text/kinetic-text-candidates.jsonl"
    for event in read_jsonl(kinetic_path):
        for obs in event.get("observations", []):
            # The heuristic identifies large text only; it cannot prove decorative text.
            add(category="kinetic_text", style="unclassified", source_kind="account_video_frame",
                source_id=event.get("source_sha256", ""), source_path=event.get("source", ""),
                time=obs.get("time"), frame_path=obs.get("frame", ""), frame_sha256=obs.get("frame_sha256", ""),
                label=obs.get("text", event.get("text_ocr", "")),
                features={"bbox_normalized": obs.get("bbox_normalized"), "relative_height": obs.get("relative_height"),
                    "vertical_zone": obs.get("vertical_zone"), "region_palette_includes_background": obs.get("region_palette_includes_background")},
                status="automatic_ocr_unreviewed",
                details={"candidate_event_id": event.get("id"), "candidate_only": True})

    for obs in read_jsonl(args.dataset / "production-visual/kinetic-text/reviewed-style-observations.jsonl"):
        # This existing file is AI visual inspection, not human gold. Preserve the actual provenance.
        add(category="kinetic_text", style="visually-observed", source_kind="account_video_frame",
            source_id=obs.get("source_sha256", ""), source_path=obs.get("source", ""),
            time=obs.get("observed_at"), frame_path=obs.get("frame", ""), frame_sha256=obs.get("frame_sha256", ""),
            label=obs.get("style_label", ""), features=obs.get("observed_style", {}),
            status="assistant_visual_inspection_unreviewed",
            details={"basis": obs.get("basis"), "scope": obs.get("scope"), "animation_reviewed": obs.get("full_animation_reviewed", False)})
        if "贴纸" in json.dumps(obs.get("observed_style", {}), ensure_ascii=False) or "贴圖" in json.dumps(obs.get("observed_style", {}), ensure_ascii=False):
            add(category="sticker", style="visually-observed", source_kind="account_video_frame",
                source_id=obs.get("source_sha256", ""), source_path=obs.get("source", ""),
                time=obs.get("observed_at"), frame_path=obs.get("frame", ""), frame_sha256=obs.get("frame_sha256", ""),
                label="与对话条同时出现的卡通贴纸", features={"co_occurs_with": obs.get("style_label")},
                status="assistant_visual_inspection_unreviewed", details={"basis": obs.get("basis"), "scope": obs.get("scope")})

    # Deduplicated official/local visual reference pack entries are evidence cards, not raw copied assets.
    for path in sorted((ROOT / "content-library/visual-reference").glob("*.json")):
        doc = read_json(path)
        for ref in doc.get("references", []):
            ref_path = ROOT / ref.get("image_path", "")
            filehash = ref.get("sha256", "")
            if not filehash and ref_path.is_file(): filehash = hashlib.sha256(ref_path.read_bytes()).hexdigest()
            add(category="kinetic_text" if "字" in doc.get("title", "") else "sticker" if "贴图" in doc.get("title", "") else "effect",
                style=path.stem, source_kind="external_visual_reference", source_id=ref.get("id", ""),
                source_path=ref.get("source_url", ""), frame_path=str(ref_path) if ref_path.is_file() else "",
                frame_sha256=filehash, label=doc.get("title", ""), features={"design_note": doc.get("text", "")},
                status=doc.get("evidence_status", "assistant_visual_analysis_unreviewed"),
                details={"production_use_allowed": doc.get("production_use_allowed", False), "reference_id": ref.get("id")})

    # Real Jianying draft resources: IDs dedupe exact resource identity; source drafts are aliases.
    draft_counts = defaultdict(int)
    if args.drafts.is_dir():
        for draft_file in args.drafts.rglob("draft_content.json"):
            try: draft = read_json(draft_file)
            except Exception: continue
            mats = draft.get("materials", {})
            if not isinstance(mats, dict): continue
            mapping = {"effects": "effect", "video_effects": "effect", "filters": "filter",
                       "stickers": "sticker", "flowers": "kinetic_text", "text_effects": "kinetic_text"}
            for material_key, category in mapping.items():
                items = mats.get(material_key, [])
                if not isinstance(items, list): continue
                for mat in items:
                    if not isinstance(mat, dict): continue
                    rid = str(mat.get("resource_id") or mat.get("effect_id") or mat.get("id") or "")
                    if not rid: continue
                    name = str(mat.get("name") or mat.get("category_name") or mat.get("type") or "(unnamed resource)")
                    add(category=category, style="jianying-resource", source_kind="jianying_draft",
                        source_id=rid, source_path=str(draft_file), label=name,
                        features={k: mat.get(k) for k in ("type", "category_name", "category_key", "effect_id", "resource_id", "value", "visible") if k in mat},
                        status="draft_resource_metadata_unreviewed", details={"material_array": material_key, "draft_id": draft_file.parent.name})
                    draft_counts[category] += 1

    # The project's own historical draft analysis captured applied filter names
    # and usage counts; treat it as aggregated metadata, not frame-level gold.
    habits_path = ROOT / "data/all_drafts_analysis.json"
    if habits_path.is_file():
        try: habits = read_json(habits_path)
        except Exception: habits = {}
        for name, count in habits.get("habits", {}).get("top_filters", []):
            add(category="filter", style="jianying-history", source_kind="draft_analysis_summary",
                source_id="all_drafts_analysis", source_path=str(habits_path), label=str(name),
                features={"observed_usage_count": count}, status="historical_draft_analysis_unreviewed",
                details={"scope": "aggregate filter usage; no linked source frame"})
            draft_counts["filter"] += int(count or 0)

    # Add the registered Jianying resource catalog (enumerated resource identities).
    registry_path = ROOT / "config/resource_registry.json"
    if registry_path.is_file():
        try: registry = read_json(registry_path)
        except Exception: registry = {}
        category_translate = {"effects": "effect", "filters": "filter", "stickers": "sticker",
                              "text_styles": "kinetic_text", "text_templates": "kinetic_text"}
        for item in registry.get("entries", []):
            category = category_translate.get(item.get("category"))
            if not category: continue
            rid = str(item.get("resource_id") or item.get("id") or "")
            if not rid: continue
            add(category=category, style="jianying-resource-registry", source_kind="jianying_resource_registry",
                source_id=rid, source_path=str(registry_path), label=str(item.get("name") or ""),
                features={k: item.get(k) for k in ("category", "resource_id", "effect_id", "source") if k in item},
                status="resource_registry_unreviewed", details={"notes": item.get("notes"), "verified_at": item.get("verified_at")})
            draft_counts[category] += 1

    # Categorized indexes contain IDs only, so multi-category references don't duplicate evidence.
    for style, categories in sorted(category_refs.items()):
        target = styles_dir / f"{style}.json"
        target.write_text(json.dumps({"style_id": style, "style_name": STYLE_LABELS.get(style, style),
            "categories": {k: {"name": CATEGORY_MAP[k], "evidence_ids": sorted(v)} for k, v in sorted(categories.items())}}, ensure_ascii=False, indent=2), encoding="utf-8")
    master = out / "evidence.jsonl"
    for row in records.values():
        row["category_labels"] = [CATEGORY_MAP[c] for c in row["categories"]]
        row["style_labels"] = [STYLE_LABELS.get(s, s) for s in row["style_ids"]]
    master.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for _, v in sorted(records.items())), encoding="utf-8")
    summary = {"version": "visual-style-db-v1", "records_unique": len(records),
        "category_memberships": {c: sum(c in r["categories"] for r in records.values()) for c in CATEGORY_MAP},
        "styles": [{"id": s, "name": STYLE_LABELS.get(s, s), "categories": sorted(category_refs[s])} for s in sorted(category_refs)],
        "draft_resources_by_category": dict(draft_counts),
        "dedup": {"key": "exact frame SHA-256; otherwise resource/source identity", "identical_evidence_deduplicated": True,
                  "category_and_style_indexes_reference_master_ids": True},
        "limits": ["sparse OCR big-text candidates are not verified flower-text", "draft resource metadata does not guarantee local cache assets exist",
                   "frame appearance alone cannot prove which filter/effect was applied", "historical draft filter summary is aggregate, not linked to source video frames", "no model weights trained"],
        "source_video_modified": False, "evidence_file": str(master)}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = """# 视觉风格证据库\n\n按风格分索引，主证据只保存一次；各分类索引仅引用证据 ID。证据包括本机成片的抽帧与 OCR、剪映草稿中真实出现的素材资源元数据，以及已有视觉参考卡。\n\n分类：花字、贴图、滤镜、特效。状态字段区分自动 OCR、AI 静帧观察、草稿资源元数据与外部视觉参考，不将它们冒充人工金标。`source_path` 可追溯原片/草稿；成片证据保留时间点和帧 SHA-256。\n\n去重按精确帧 SHA-256 或素材资源 ID；同证据可同时归入多个类别/风格，但主库仅保留一条。风格未能可靠识别时归入 `unclassified`，不会凭空推断。\n\n成片抽帧每约 5 秒采样，无法确认逐帧动态、转场全貌或具体滤镜参数。草稿资源元数据不保证缓存仍可用。素材版权状态尚未核实，仅限本机分析；尚未训练模型权重。\n"""
    (out / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
