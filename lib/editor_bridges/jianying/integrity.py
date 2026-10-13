"""AI CUT original draft checks and opt-in, hash-verified media packaging.

Research notes: docs/jianying-editor-integration.md. No upstream code vendored.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path


def validate_ir(project):
    if not all(math.isfinite(x) and x > 0 for x in (project.fps, project.width, project.height)):
        raise ValueError("Invalid canvas or frame rate")
    if not math.isfinite(project.duration) or project.duration < 0:
        raise ValueError("Invalid project duration")
    ids = set()
    for track in project.tracks:
        previous_end = 0
        for seg in sorted(track.segments, key=lambda x: x.t):
            numbers = [seg.t, seg.length, seg.speed]
            numbers += [x for x in (seg.src_in, seg.src_len) if x is not None]
            if not all(math.isfinite(x) for x in numbers):
                raise ValueError("Non-finite segment timing")
            if seg.t < 0 or seg.length <= 0 or seg.speed <= 0:
                raise ValueError("Invalid segment timing")
            if seg.volume is not None and (not math.isfinite(seg.volume) or not 0 <= seg.volume <= 6):
                raise ValueError("Invalid segment volume")
            if seg.src_len is not None and abs(seg.src_len / seg.speed - seg.length) > .000002:
                raise ValueError("Source duration / speed must equal target duration")
            if seg.src_in is not None and seg.src_in < 0 or seg.src_len is not None and seg.src_len <= 0:
                raise ValueError("Invalid source trim")
            if seg.track_type != track.type:
                raise ValueError("Segment and track types differ")
            if seg.t < previous_end - 0.000001:
                raise ValueError("Overlapping segments require separate tracks")
            previous_end = seg.end
            if seg.id and seg.id in ids:
                raise ValueError("Duplicate segment ID")
            ids.add(seg.id)
            if not (seg.text if track.type == "text" else seg.material):
                raise ValueError("Missing segment material or text")


def check_content(data):
    errors = []
    if not isinstance(data, dict):
        return ["Draft root must be an object"]
    project_duration = data.get("duration")
    if not isinstance(project_duration, int) or isinstance(project_duration, bool) or project_duration <= 0:
        errors.append("Invalid project duration")
        project_duration = 0
    materials = data.get("materials", {})
    if not isinstance(materials, dict):
        return ["materials must be an object"]
    ids = set()
    for group in materials.values():
        if not isinstance(group, list):
            errors.append("Material group must be an array")
            continue
        for mat in group:
            if not isinstance(mat, dict) or not mat.get("id"):
                errors.append("Material missing ID")
                continue
            if mat["id"] in ids:
                errors.append("Duplicate material ID")
            ids.add(mat["id"])
    tracks = data.get("tracks")
    if not isinstance(tracks, list):
        return errors + ["tracks must be an array"]
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("segments"), list):
            errors.append("Malformed track segments")
            continue
        for seg in track["segments"]:
            if not isinstance(seg, dict):
                errors.append("Segment must be an object")
                continue
            if seg.get("material_id") not in ids:
                errors.append("Dangling material reference")
            for ref in seg.get("extra_material_refs") or []:
                if ref not in ids:
                    errors.append("Dangling extra material reference")
            tr = seg.get("target_timerange", {})
            if not isinstance(tr, dict):
                errors.append("Invalid target time range")
                continue
            start, duration = tr.get("start"), tr.get("duration")
            if not isinstance(start, int) or not isinstance(duration, int) or start < 0 or duration <= 0:
                errors.append("Invalid target timing")
            elif start + duration > project_duration:
                errors.append("Segment exceeds project duration")
    return errors


def _digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def prepare_assets(content, folder, media_root=None):
    """Copy only explicitly referenced local media, never modify source files."""
    folder = Path(folder).resolve()
    pending = []
    for group in content["materials"].values():
        for mat in group:
            if not mat.get("path"):
                continue
            source = Path(mat["path"])
            if not source.is_absolute():
                if not media_root:
                    raise ValueError("Relative media requires project.extra.media_root")
                source = Path(media_root) / source
            source = source.resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            digest = _digest(source)
            target = folder / "media" / (digest + source.suffix.lower())
            pending.append((mat, source, target, digest))
    rows = {}
    for mat, source, target, digest in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        if _digest(target) != digest:
            raise ValueError("Packaged media hash mismatch")
        mat["path"] = str(target)
        rows[str(target.relative_to(folder))] = {
            "sha256": digest, "bytes": target.stat().st_size,
        }
    (folder / "assets.manifest.json").write_text(json.dumps({
        "schema": "aicut-draft-assets-1", "assets": rows,
        "host_import_verified": False,
        "relocation": "Paths are absolute; regenerate or rebase when moving the folder.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
