"""Validate and summarize the provenance-only public dataset catalog."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / 'content-library/rag/public_dataset_registry.jsonl'
REQUIRED = ('id', 'title', 'category', 'source_type', 'source_url', 'license',
            'license_status', 'access_status', 'usage_status', 'summary',
            'fit_for_aicut', 'limitations', 'checked_at')


def main() -> int:
    if not REGISTRY.is_file():
        print(f'MISSING: {REGISTRY}')
        return 2
    categories, usage, ids = Counter(), Counter(), set()
    errors = []
    with REGISTRY.open(encoding='utf-8-sig') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('record is not an object')
                missing = [key for key in REQUIRED if not isinstance(row.get(key), str) or not row[key].strip()]
                if missing:
                    raise ValueError('missing fields: ' + ', '.join(missing))
                if not row['source_url'].startswith('https://'):
                    raise ValueError('source_url must be HTTPS')
                if row['id'] in ids:
                    raise ValueError('duplicate id')
                ids.add(row['id'])
                categories[row['category']] += 1
                usage[row['usage_status']] += 1
            except (ValueError, TypeError) as exc:
                errors.append(f'line {number}: {exc}')
    if errors:
        print('\n'.join('ERROR ' + error for error in errors))
        return 1
    print(f'valid_records={len(ids)}')
    print('categories=' + json.dumps(dict(sorted(categories.items())), ensure_ascii=False))
    print('usage_status=' + json.dumps(dict(sorted(usage.items())), ensure_ascii=False))
    print('catalog_only=true; records are not proof of downloaded data or training rights')
    return 0


if __name__ == '__main__':
    sys.exit(main())
