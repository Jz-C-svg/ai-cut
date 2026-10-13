"""Read-only integrity and retrieval audit for canonical paired visual studies."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aicut import learning_context


def audit(root: Path, query: str) -> dict:
    registry = root / 'content-library/web-visuals/paired-samples/studies.jsonl'
    raw = registry.read_text(encoding='utf-8').splitlines()
    records = sum(bool(line.strip()) for line in raw)
    # Force current media hashes to be rechecked, including in long-lived callers.
    learning_context._CACHE.pop(str(root.resolve()), None)
    catalog = learning_context.catalog(root)
    studies = [d for d in catalog['docs'] if d['category'] == 'paired_visual_study']
    warnings = [w for w in catalog['warnings'] if 'paired visual study' in w]
    bundle = learning_context.retrieve(query, root=root, budget=26000, top_k=14)
    selected = learning_context.selected_evidence(bundle)
    indexed = {d['id'] for d in studies}
    selected_ids = [d['id'] for d in selected if d['category'] == 'paired_visual_study']
    return {
        'checked_at': datetime.now().astimezone().isoformat(),
        'passed': records == len(studies) and not warnings and set(selected_ids) <= indexed,
        'registry_records': records, 'valid_unique_studies': len(studies),
        'warnings': warnings, 'query': query, 'selected_paired_studies': selected_ids,
        'studies': [{
            'id': d['id'], 'source_url': d['source_url'],
            'published_at': d.get('published_at'), 'video_sha256': d['video_sha256'],
            'decoded_frames_reported': d['video_metadata']['decoded_frames'],
            'observed_sampled_frames': len(d['frame_observations']),
            'review_status': d['review_status'],
        } for d in studies],
        'limitations': ['Hash and record checks do not replace a new ffmpeg decode.',
                       'Retrieval success does not prove model reasoning or final rendered quality.',
                       'Observed sampled frames are not full-frame semantic coverage.'],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--query', default='短视频教学 运镜前后对比 花字 字幕 构图 剪辑')
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()
    result = audit(args.root.resolve(), args.query)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding='utf-8')
    print(text)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
