"""Validate public platform summaries and keep their scope attached to evidence."""
import hashlib
import json
import re


def selected_platforms(query, platform=None):
    if platform not in (None, 'douyin', 'bilibili', 'all'):
        raise ValueError('platform must be douyin, bilibili or all')
    if platform:
        return {'douyin', 'bilibili'} if platform == 'all' else {platform}
    found = set()
    if re.search(r'抖音|\bdouyin\b', query, re.I):
        found.add('douyin')
    if re.search(r'哔哩哔哩|b\s*站|\bbilibili\b', query, re.I):
        found.add('bilibili')
    return found


def read_records(raw, import_path, existing_ids, warnings):
    seen = set(existing_ids)
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or row.get('tier') != 'platform_reference':
                raise ValueError('wrong tier')
            if row.get('default_retrieval') is not True:
                raise ValueError('historical record excluded')
            if row.get('platform') not in {'douyin', 'bilibili'}:
                raise ValueError('unknown platform')
            required = ('id', 'title', 'text', 'source_url', 'source_title', 'source_locator',
                        'checked_at', 'access_mode', 'scope', 'limitations', 'content_sha256')
            if any(not isinstance(row.get(k), str) or not row[k].strip() for k in required):
                raise ValueError('missing metadata')
            if not row['source_url'].startswith('https://') or row['id'] in seen:
                raise ValueError('bad source or duplicate')
            if hashlib.sha256(row['text'].encode()).hexdigest() != row['content_sha256']:
                raise ValueError('hash mismatch')
            doc = {k: row.get(k) for k in (
                'id', 'title', 'text', 'platform', 'scope', 'limitations', 'source_url',
                'source_title', 'source_date', 'checked_at', 'access_mode', 'knowledge_type')}
            doc.update(source=row['source_url'], locator=row['source_locator'],
                       category='platform_public', tier='platform_reference',
                       import_path=import_path, requires_live_check=True)
            seen.add(row['id'])
            yield doc
        except (ValueError, TypeError, KeyError):
            warnings.append(f'{import_path}:{line_number}: invalid or duplicate platform record')
