"""Select and extract a compact, class-diverse test set from AutoTransition shards."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "content-library/web-visuals/external-datasets/autotransition"
EVENTS = BASE / "normalized/transition-events.jsonl"
RAW = BASE / "raw"
OUT = BASE / "extracted-research"


class ShardReader(io.RawIOBase):
    """Read numbered shard files as one logical byte stream, without joining them."""

    def __init__(self, shards: list[Path]):
        self.shards = shards
        self.index = 0
        self.current = shards[0].open("rb") if shards else None
        self._readable = True

    def readable(self) -> bool:
        return self._readable

    def readinto(self, buffer) -> int:
        view = memoryview(buffer)
        written = 0
        while written < len(view) and self.current is not None:
            count = self.current.readinto(view[written:])
            if count:
                written += count
                continue
            self.current.close()
            self.index += 1
            self.current = self.shards[self.index].open("rb") if self.index < len(self.shards) else None
        return written

    def close(self):
        if self.current is not None:
            self.current.close()
            self.current = None
        super().close()


def select_events(max_videos: int) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    by_video: dict[str, list[dict]] = defaultdict(list)
    class_counts: dict[str, int] = defaultdict(int)
    with (BASE / "normalized/transition-events.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if (row["split"] == "test" and row["event_kind"] == "effect_interval"
                    and row["time_validation"] == "valid" and 120 <= row["duration_ms"] <= 2400):
                by_video[row["video_id"]].append(row)
                class_counts[row["transition_class"]] += 1
    selected: dict[str, list[dict]] = {}
    covered: set[str] = set()
    remaining = set(by_video)
    for _ in range(max_videos):
        best_id, best_rows, best_gain = None, [], 0
        for video_id in remaining:
            rows = by_video[video_id]
            classes = {row["transition_class"] for row in rows}
            gain = len(classes - covered)
            if gain > best_gain:
                best_id, best_rows, best_gain = video_id, rows, gain
        if best_id is None:
            break
        selected[best_id] = best_rows
        covered.update(row["transition_class"] for row in best_rows)
        remaining.remove(best_id)
    class_index = {name: {"test_event_count": count, "selected": name in covered}
                   for name, count in sorted(class_counts.items())}
    return selected, class_index


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-videos", type=int, default=12)
    args = parser.parse_args()
    shards = sorted(RAW.glob("template_download.tar.gz.[0-9][0-9]"))
    if len(shards) != 13:
        raise SystemExit(f"expected 13 verified shards; found {len(shards)}")
    audit_path = BASE / "archive-audit.json"
    if not audit_path.is_file() or not json.loads(audit_path.read_text(encoding="utf-8")).get("archive_complete"):
        raise SystemExit("archive must pass publisher SHA-256 audit before extraction")

    selected, class_index = select_events(args.max_videos)
    OUT.mkdir(parents=True, exist_ok=True)
    wanted_ids = set(selected)
    extracted = {}
    with ShardReader(shards) as joined:
        with tarfile.open(fileobj=io.BufferedReader(joined), mode="r|") as archive:
            for member in archive:
                if not member.isfile() or member.size <= 0:
                    continue
                parts = Path(member.name).parts
                video_id = next((part for part in parts if part in wanted_ids), None)
                if video_id is None:
                    continue
                suffix = Path(member.name).suffix.lower()
                if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
                    continue
                destination = OUT / video_id / f"source{suffix}"
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    source = archive.extractfile(member)
                    if source is None:
                        continue
                    with source, destination.open("wb") as target:
                        while True:
                            block = source.read(4 * 1024 * 1024)
                            if not block:
                                break
                            target.write(block)
                extracted[video_id] = {"archive_member": member.name,
                                       "path": destination.relative_to(ROOT).as_posix(),
                                       "bytes": destination.stat().st_size,
                                       "sha256": sha256(destination),
                                       "events": selected[video_id]}
                if wanted_ids.issubset(extracted):
                    break
    manifest = {"schema_version": "autotransition-visual-sample-1",
                "dataset_revision": "5160dbd50e89cb2c982790e8e65bb91664d9063e",
                "license_scope": "internal research only; original source rights unverified",
                "retrieval_eligible": False,
                "selected_video_count": len(selected), "extracted_video_count": len(extracted),
                "covered_transition_classes": sorted({e["transition_class"] for row in extracted.values() for e in row["events"]}),
                "class_inventory_test_split": class_index, "videos": extracted}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("selected_video_count", "extracted_video_count", "covered_transition_classes")}, ensure_ascii=False))
    return 0 if len(extracted) == len(selected) else 2


if __name__ == "__main__":
    raise SystemExit(main())
