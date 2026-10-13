"""Strict full-stream decoding before editorial inference (no frame sampling)."""
from __future__ import annotations

import subprocess
import time
import json
from pathlib import Path

from .paths import WORK, ffmpeg, fingerprint


def resolve_original(video: str | Path) -> Path:
    """Follow only manifest-owned source links; never guess a same-named file."""
    current = Path(video).resolve(strict=True)
    manifest = WORK / 'manifest.json'
    if not manifest.exists():
        return current
    items = json.loads(manifest.read_text(encoding='utf-8-sig')).get('items', [])
    seen = set()
    while current not in seen:
        seen.add(current)
        match = next((row for row in items if row.get('video')
                      and Path(row['video']).resolve() == current), None)
        if not match or not match.get('source'):
            return current
        source = Path(match['source']).resolve(strict=True)
        if source == current:
            return current
        current = source
    raise ValueError('Cyclic source links in material manifest')


def audit_decode(video: str | Path) -> dict:
    video = Path(video).resolve(strict=True)
    started = time.monotonic()
    proc = subprocess.run(
        [ffmpeg(), '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
         '-err_detect', 'explode', '-threads', '2', '-i', str(video), '-map', '0:v:0',
         '-map', '0:a?', '-fps_mode:v', 'passthrough', '-progress', 'pipe:1', '-nostats', '-f', 'null', '-'],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    progress = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition('=')
        if sep:
            progress[key.strip()] = value.strip()
    frames = int(progress.get('frame', '0'))
    complete = proc.returncode == 0 and progress.get('progress') == 'end' and frames > 0
    return {'schema': 1, 'video': str(video), 'fingerprint': fingerprint(video),
            'complete': complete, 'decoded_video_frames': frames,
            'end_time': progress.get('out_time'), 'returncode': proc.returncode,
            'decoder_error': proc.stderr[-4000:],
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'scope': 'full video and audio decode; not semantic understanding'}
