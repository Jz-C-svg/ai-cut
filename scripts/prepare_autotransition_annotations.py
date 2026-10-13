"""Normalize the published AutoTransition annotations for auditable curation.

This creates an annotation index only. It does not download source-platform
videos, assert individual video rights, or add records to the production RAG.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "content-library/web-visuals/external-datasets/autotransition/transition_annotation_en_url_fixed.json"
DEFAULT_OUT = ROOT / "content-library/web-visuals/external-datasets/autotransition/normalized"
REPO = "https://huggingface.co/datasets/yaojie-shen/AutoTransition"
REVISION = "5160dbd50e89cb2c982790e8e65bb91664d9063e"


def normalize(source: Path, out: Path) -> dict:
    raw_bytes = source.read_bytes()
    raw = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("templates"), dict):
        raise ValueError("AutoTransition annotation schema must contain templates")
    events = []
    class_rows: dict[str, dict] = defaultdict(lambda: {"train": 0, "test": 0, "zero_duration": 0,
                                                        "positive_duration": 0, "invalid_time": 0,
                                                        "sample_ids": []})
    split_counts = {}
    seen_ids: set[str] = set()
    for split, templates in raw["templates"].items():
        if split not in {"train", "test"} or not isinstance(templates, dict):
            raise ValueError(f"unexpected dataset split: {split}")
        split_counts[split] = len(templates)
        for source_id, item in templates.items():
            source_id = str(source_id)
            if source_id in seen_ids:
                raise ValueError(f"duplicate video id across splits: {source_id}")
            seen_ids.add(source_id)
            if not isinstance(item, dict) or not isinstance(item.get("transition"), list):
                raise ValueError(f"invalid transition list for video {source_id}")
            source_url = str(item.get("url") or "")
            for ordinal, event in enumerate(item["transition"]):
                if not isinstance(event, dict) or not event.get("name"):
                    raise ValueError(f"invalid transition event for video {source_id}")
                start, end = int(event.get("start", -1)), int(event.get("end", -1))
                duration = int(event.get("duration", end - start))
                time_valid = start >= 0 and end >= start and duration >= 0
                name = str(event["name"])
                stats = class_rows[name]
                stats[split] += 1
                if not time_valid:
                    stats["invalid_time"] += 1
                stats["zero_duration" if duration == 0 else "positive_duration"] += 1
                if len(stats["sample_ids"]) < 5:
                    stats["sample_ids"].append(source_id)
                events.append({
                    "video_id": source_id, "split": split, "event_index": ordinal,
                    "transition_class": name, "start_ms": start, "end_ms": end,
                    "duration_ms": duration,
                    "event_kind": ("invalid_annotation" if not time_valid else
                                   "cut_boundary" if name == "direct_cut" or duration == 0 else "effect_interval"),
                    "time_validation": "valid" if time_valid else "out_of_bounds_or_reversed",
                    "source_url": source_url,
                    "evidence_status": "annotation_only_video_frame_pair_pending",
                    "retrieval_eligible": False,
                })
    if not events:
        raise ValueError("annotation dataset contains no transition events")

    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / "transition-events.jsonl"
    with jsonl.open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    classes = []
    for name, counts in sorted(class_rows.items(), key=lambda x: (-x[1]["train"]-x[1]["test"], x[0])):
        classes.append({"name": name, **counts, "total": counts["train"] + counts["test"]})
    catalog = {
        "schema_version": "autotransition-catalog-1",
        "dataset": "AutoTransition: Learning to Recommend Video Transition Effects",
        "dataset_url": REPO,
        "dataset_revision": REVISION,
        "dataset_page_license": "CC-BY-4.0",
        "license_scope_note": "Dataset page labels the dataset CC BY 4.0; original source-platform content and per-video rights have not been independently verified. Internal research only; no production use or redistribution.",
        "annotation_source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "collected_date": date.today().isoformat(),
        "split_video_counts": split_counts,
        "video_count": len(seen_ids),
        "transition_event_count": len(events),
        "transition_class_count": len(classes),
        "zero_duration_event_count": sum(x["zero_duration"] for x in classes),
        "invalid_time_event_count": sum(x["invalid_time"] for x in classes),
        "source_hosts": sorted({e["source_url"].split("/")[2] for e in events if e["source_url"]}),
        "visual_pairs_collected": 0,
        "retrieval_eligible_events": 0,
        "classes": classes,
    }
    catalog_path = out / "transition-classes.json"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {k: v for k, v in catalog.items() if k != "classes"}
    manifest["normalized_events_sha256"] = hashlib.sha256(jsonl.read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = normalize(args.source, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
