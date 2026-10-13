"""Collect explicitly selected public reference images with provenance, never production assets."""
import concurrent.futures
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'work/lettering-reference-scan-20261001'


def main():
    rows = json.loads((OUT / 'sources.json').read_text(encoding='utf-8'))
    images = OUT / 'images'
    images.mkdir(exist_ok=True)
    previous = {r['id']: r for r in json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))} if (OUT / 'manifest.json').exists() else {}
    def collect(row):
        old = previous.get(row['id'])
        if old and old.get('image_url') == row['image_url']:
            if old.get('status') == 'failed':
                return old
            path = Path(old.get('file', ''))
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == old.get('sha256'):
                return old
        result = dict(row, checked_at=datetime.now(timezone.utc).isoformat(),
            license_status='not_verified_reference_only', production_use_allowed=False,
            human_reviewed=False, provenance='external_reference_unreviewed')
        try:
            response = requests.get(row['image_url'], timeout=(8, 25), stream=True)
            response.raise_for_status()
            data = bytearray()
            for block in response.iter_content(65536):
                data.extend(block)
                if len(data) > 20_000_000:
                    raise ValueError('reference exceeds 20 MB limit')
            im = Image.open(io.BytesIO(data))
            width, height = im.size
            frames = getattr(im, 'n_frames', 1)
            ext = {'JPEG': '.jpg', 'PNG': '.png', 'GIF': '.gif', 'WEBP': '.webp'}[im.format]
            im.verify()
            path = images / (row['id'] + ext)
            path.write_bytes(data)
            result.update(status='collected', file=str(path), sha256=hashlib.sha256(data).hexdigest(),
                          width=width, height=height, frames=frames, bytes=len(data))
        except Exception as exc:
            result.update(status='failed', error=f'{type(exc).__name__}: {str(exc)[:180]}')
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(collect, rows))
    seen = {}
    for row in results:
        if row.get('sha256') in seen:
            row['duplicate_of'] = seen[row['sha256']]
        elif row.get('sha256'):
            seen[row['sha256']] = row['id']
    (OUT / 'manifest.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    for row in results:
        print(row['id'], row['status'], row.get('width'), row.get('height'), row.get('frames'), flush=True)


if __name__ == '__main__':
    main()
