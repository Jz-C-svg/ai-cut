"""Verify the pinned AutoTransition shard set without extracting the archive."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "content-library/web-visuals/external-datasets/autotransition/raw"
REVISION = "5160dbd50e89cb2c982790e8e65bb91664d9063e"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit(directory: Path) -> dict:
    tree_path = directory / ".cache/huggingface/trees" / f"{REVISION}.json"
    tree = json.loads(tree_path.read_text(encoding="utf-8"))
    expected = {}
    for name, metadata in tree.get("files", {}).items():
        if name.startswith("template_download.tar.gz."):
            suffix = name.rsplit(".", 1)[-1]
            if suffix.isdigit():
                expected[int(suffix)] = metadata
    if not expected:
        raise ValueError(f"pinned shard metadata missing: {tree_path}")
    parts = []
    for index, metadata in sorted(expected.items()):
        expected_size = int(metadata["size"])
        expected_sha256 = metadata.get("lfs_sha256")
        path = directory / f"template_download.tar.gz.{index:02d}"
        if not path.is_file():
            parts.append({"part": index, "state": "missing", "expected_bytes": expected_size,
                          "expected_sha256": expected_sha256})
            continue
        size = path.stat().st_size
        complete = size == expected_size
        actual_sha256 = sha256(path) if complete else None
        hash_matches = actual_sha256 == expected_sha256 if expected_sha256 else None
        state = "complete" if complete and hash_matches is not False else "size_or_hash_mismatch" if complete else "partial"
        parts.append({"part": index, "state": state,
                      "bytes": size, "expected_bytes": expected_size,
                      "sha256": actual_sha256, "expected_sha256": expected_sha256,
                      "publisher_sha256_match": hash_matches})
    return {"schema_version": "autotransition-archive-audit-1", "directory": str(directory),
            "parts": parts, "complete_parts": sum(p["state"] == "complete" for p in parts),
            "part_count": len(expected), "archive_complete": all(p["state"] == "complete" for p in parts),
            "dataset_revision": REVISION,
            "note": "Each shard size and SHA-256 is compared to the pinned Hugging Face LFS metadata."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = audit(args.directory)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["archive_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
