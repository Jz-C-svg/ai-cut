"""Merge an existing AI cut transcript JSON into Video-RAG records by time overlap."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("record", type=Path)
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    data = [json.loads(x) for x in args.record.read_text(encoding="utf-8").splitlines() if x.strip()]
    tr = json.loads(args.transcript.read_text(encoding="utf-8"))
    rows = tr.get("segments", tr if isinstance(tr, list) else [])
    for item in data:
        for seg in item.get("segments", []):
            hits = [r for r in rows if float(r.get("start", 0)) < seg["end"] and float(r.get("end", 0)) > seg["start"]]
            seg["transcript"] = " ".join(str(r.get("text", "")).strip() for r in hits).strip()
            seg["analysis_status"] = "asr_enriched"
            seg["asr_source"] = str(args.transcript)
    out = args.output or args.record
    out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in data) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "records": len(data), "transcript_segments": len(rows), "status": "enriched"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
