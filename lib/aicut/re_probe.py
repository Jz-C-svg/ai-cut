"""Bounded, read-only format and PE inspection for editor interoperability."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

LIMIT = 64 * 1024 * 1024
FILES = ('draft_content.json', 'draft_content.json.bak', 'draft_meta_info.json',
         'draft_meta_info.json.bak', 'template.tmp', 'template-2.tmp')


def _linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def probe_file(path):
    path = Path(path).absolute()
    result = {'path': str(path), 'kind': 'missing'}
    if _linked(path) or any(_linked(p) for p in path.parents):
        return {**result, 'kind': 'linked_input_skipped'}
    if not path.exists():
        return result
    before = path.stat()
    if not path.is_file():
        return {**result, 'kind': 'not_regular_file'}
    with path.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        stream.seek(0)
        data = stream.read(LIMIT + 1)
    after = path.stat()
    result.update(size=before.st_size, mtime_ns=before.st_mtime_ns, sha256=checksum)
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        return {**result, 'kind': 'unstable_during_read'}
    if before.st_size > LIMIT:
        return {**result, 'kind': 'too_large_to_parse'}
    if not data:
        return {**result, 'kind': 'empty'}
    try:
        text = data.decode('utf-8-sig')
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        # File shape is evidence, never a claim about an encryption algorithm.
        stripped = data.removeprefix(b'\xef\xbb\xbf').lstrip()
        if stripped[:1] in (b'{', b'['):
            return {**result, 'kind': 'malformed_json'}
        try:
            base64.b64decode(b''.join(stripped.split()), validate=True)
            shape = len(stripped) >= 32
        except (ValueError, base64.binascii.Error):
            shape = False
        magic = next((label for prefix, label in ((b'PK\x03\x04', 'zip'), (b'\x1f\x8b', 'gzip'),
                                                  (b'7z\xbc\xaf\x27\x1c', '7z')) if data.startswith(prefix)), None)
        return {**result, 'kind': 'known_archive_or_compression' if magic else
                'opaque_base64_like' if shape else 'unknown_binary', **({'container': magic} if magic else {})}
    if isinstance(value, dict) and isinstance(value.get('tracks'), list):
        tracks = value['tracks']
        nonempty = any(t.get('segments') for t in tracks if isinstance(t, dict))
        return {**result, 'kind': 'json_project' if nonempty else 'json_empty_template',
                'summary': {'duration_us': value.get('duration'), 'fps': value.get('fps'),
                            'track_types': [t.get('type') for t in tracks if isinstance(t, dict)],
                            'segment_count': sum(len(t.get('segments') or []) for t in tracks if isinstance(t, dict))}}
    return {**result, 'kind': 'json_non_project'}


def probe_project(folder):
    folder = Path(folder).absolute()
    if _linked(folder):
        raise ValueError('Project cannot be a symbolic link or junction')
    folder = folder.resolve(strict=True)
    files = [probe_file(folder / name) for name in FILES]
    timelines = folder / 'Timelines'
    if timelines.is_dir() and not _linked(timelines):
        # Explicit depth and count bounds; never walk caches, credentials or assets.
        for entry in sorted(timelines.iterdir())[:100]:
            if entry.is_dir() and not _linked(entry):
                files.extend(probe_file(entry / name) for name in FILES[:2])
    groups = {}
    for item in files:
        if item.get('sha256'):
            groups.setdefault(item['sha256'], []).append(item['path'])
    return {'schema': 'aicut-draft-probe-1', 'project': str(folder), 'read_only': True, 'files': files,
            'duplicate_groups': [v for v in groups.values() if len(v) > 1],
            'executed_in_editor': False, 'native_export_verified': False}


def inspect_pe(binary):
    import pefile
    from importlib.metadata import version
    binary = Path(binary).resolve(strict=True)
    with binary.open('rb') as stream:
        before = hashlib.file_digest(stream, 'sha256').hexdigest()
    pe = pefile.PE(str(binary), fast_load=True, max_symbol_exports=200000)
    try:
        pe.parse_data_directories(directories=[0, 1])
        exports = getattr(getattr(pe, 'DIRECTORY_ENTRY_EXPORT', None), 'symbols', [])
        relevant = [{'name': s.name.decode('utf-8', 'replace'), 'rva': hex(s.address)} for s in exports
                    if s.name and (b'@EncryptUtils@' in s.name or b'ReadJsonFromFile' in s.name)]
        report = {'schema': 'aicut-pe-probe-1', 'binary': str(binary), 'sha256': before,
                  'size': binary.stat().st_size, 'tool': 'pefile', 'tool_version': version('pefile'),
                  'tool_source': 'https://github.com/erocarrera/pefile',
                  'machine': hex(pe.FILE_HEADER.Machine), 'export_count': len(exports),
                  'export_limit': 200000, 'exports_may_be_truncated': len(exports) >= 200000,
                  'relevant_exports': relevant,
                  'import_libraries': sorted({e.dll.decode('ascii', 'replace') for e in
                                              getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])}),
                  'target_executed': False}
    finally:
        pe.close()
    with binary.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != before:
            raise ValueError('Binary changed during static inspection')
    return report


def write_report(report, output):
    output = Path(output).resolve()
    inspected = Path(report.get('project') or report.get('binary')).resolve()
    if output == inspected or inspected in output.parents:
        raise ValueError('Report must be outside the inspected project or binary')
    from .production import atomic_json
    atomic_json(output, report)
    return output
