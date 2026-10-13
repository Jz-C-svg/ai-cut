"""Validate AIcut video RAG JSONL records against the project contract."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def validate(path: Path) -> tuple[int, list[str]]:
    errors: list[str] = []
    count = 0
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        count += 1
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_no}: invalid JSON ({exc.msg})")
            continue
        for key in ("video_id", "platform", "source", "segments"):
            if key not in item:
                errors.append(f"line {line_no}: missing {key}")
        source = item.get("source", {})
        for key in ("url", "title", "license_status"):
            if key not in source:
                errors.append(f"line {line_no}: source missing {key}")
        for seg in item.get("segments", []):
            if seg.get("end", 0) < seg.get("start", 0):
                errors.append(f"line {line_no}: segment end before start")
    return count, errors


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("content-library/rag/video_rag.jsonl")
    count, errors = validate(target)
    print(json.dumps({"file": str(target), "records": count, "valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)
