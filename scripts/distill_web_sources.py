"""将外部项目注册表蒸馏为可检索 JSONL，保留来源与授权边界。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="content-library/rag/web_distillation_sources.jsonl")
    ap.add_argument("--output", default="content-library/rag/web_distilled_knowledge.jsonl")
    args = ap.parse_args()
    source = Path(args.input)
    output = Path(args.output)
    seen: set[str] = set()
    rows: list[dict] = []
    for raw in source.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        item = json.loads(raw)
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        text = "；".join(item.get("distill_targets", []))
        retrieval = f"项目：{item['title']}\n类型：{item['kind']}\n主题：{'、'.join(item.get('topics', []))}\n可借鉴：{text}\n来源：{item['url']}"
        rows.append({
            "id": "web-distilled-" + hashlib.sha1(item["url"].encode()).hexdigest()[:12],
            "title": item["title"], "knowledge_type": "external_project_distillation",
            "text": text, "retrieval_text": retrieval, "source_url": item["url"],
            "source_tier": item.get("source_tier", "unknown"),
            "license_status": item.get("license_status", "unknown"),
            "topics": item.get("topics", []), "distilled_at": "2026-09-27"
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "records": len(rows), "deduplicated": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
