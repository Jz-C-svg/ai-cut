"""Read saved local drafts through a pinned codec in a disposable process.

No injection, process attachment, draft replacement, key extraction or upload.
Only input copies and an independent, hash-bound audit snapshot are written.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from .paths import ROOT
from .production import atomic_json

CONFIG = ROOT / 'config' / 'native_codec.local.json'
LIMIT = 64 * 1024 * 1024
NAMES = ('draft_content.json', 'draft_meta_info.json')


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _regular(path):
    path = Path(path)
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Draft inputs cannot be symbolic links or junctions')
    return path


def pinned_codec(config=CONFIG):
    data = json.loads(Path(config).read_text(encoding='utf-8'))
    if data.get('schema') != 'aicut-native-codec-1' or data.get('mode') != 'read_only':
        raise ValueError('Read-only native codec has not been configured')
    dll = Path(data['dll'])
    if not dll.is_absolute() or dll.name.lower() != 'videoeditor.dll':
        raise ValueError('Native codec requires an absolute videoeditor.dll path')
    if sha256(dll) != data['dll_sha256']:
        raise ValueError('Native codec version changed; revalidate compatibility before use')
    import pefile
    from importlib.metadata import version
    # pefile defaults to 8192 exports; this DLL has over 32000 exports.
    pe = pefile.PE(str(dll), fast_load=True, max_symbol_exports=200000)
    try:
        pe.parse_data_directories(directories=[0])
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location('aicut_native_worker_contract', ROOT / 'scripts' / 'native_draft_worker.py')
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        exports = {s.name for s in getattr(pe, 'DIRECTORY_ENTRY_EXPORT').symbols}
        if pe.FILE_HEADER.Machine != 0x8664 or module.DEC_EXPORT.encode() not in exports:
            raise ValueError('Native codec ABI/export is unsupported')
    finally:
        pe.close()
    return {**data, 'pefile_version': version('pefile')}


def decode_file(source, output, *, codec, timeout=30):
    """Decode only an already copied canonical input; reject overwrite."""
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    if source.name not in NAMES or output.exists() or source == output:
        raise ValueError('Expected a canonical input and new independent output')
    if source.stat().st_size > LIMIT:
        raise ValueError('Draft exceeds decoder limit')
    before = sha256(source)
    result_path = output.with_name(output.name + '.worker-result.json')
    if result_path.exists():
        raise ValueError('Worker result already exists')
    request = {'dll': codec['dll'], 'dll_sha256': codec['dll_sha256'],
               'source': str(source), 'source_sha256': before, 'output': str(output)}
    try:
        proc = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'scripts' / 'native_draft_worker.py'),
                               str(result_path)], input=json.dumps(request), text=True, encoding='utf-8',
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Native draft worker timed out; original draft was not modified') from exc
    if proc.returncode or not result_path.is_file():
        raise ValueError('Native draft worker failed; original draft was not modified')
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if (not result.get('ok') or result.get('source_sha256') != before or sha256(source) != before
            or result.get('dll_sha256') != codec['dll_sha256'] or sha256(output) != result.get('sha256')):
        raise ValueError('Native draft worker provenance check failed')
    if output.stat().st_size > LIMIT:
        raise ValueError('Decoded draft exceeds limit')
    if not isinstance(json.loads(output.read_text(encoding='utf-8-sig')), dict):
        raise ValueError('Decoded draft is not an object')
    return result


def snapshot(project, output_root=None, *, config=CONFIG):
    """Return a stable decoded audit copy, never select a backup or template."""
    # This host intentionally relocates the editor's whole drafts root through
    # a junction. Resolve that explicitly selected root before enforcing the
    # boundary on individual project files; never follow a linked draft file.
    project = _regular(Path(project).absolute()).resolve(strict=True)
    for parent in project.parents:
        _regular(parent)
    output_root = Path(output_root or ROOT / 'work' / 'native-readback').resolve()
    if output_root == project or project in output_root.parents:
        raise ValueError('Readback output must be outside the editor project')
    codec = None
    token = output_root / uuid.uuid4().hex
    target, copies = token / project.name, token / 'inputs'
    target.mkdir(parents=True, exist_ok=False)
    copies.mkdir()
    files = []
    try:
        for name in NAMES:
            source = _regular(project / name)
            if not source.exists():
                if name == NAMES[0]:
                    raise FileNotFoundError('Saved draft_content.json is missing')
                continue
            if not source.is_file() or source.stat().st_size > LIMIT:
                raise ValueError('Invalid or oversized draft input')
            before = sha256(source)
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != before or sha256(source) != before:
                raise ValueError('Saved draft changed while being copied')
            copied = copies / name
            copied.write_bytes(data)
            out = target / name
            try:
                plain = json.loads(data.decode('utf-8-sig'))
                if not isinstance(plain, dict):
                    raise ValueError('Saved draft is not a JSON object')
                out.write_bytes(data)
                method = 'plain_json'
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Broken JSON must not be misdiagnosed as an encrypted project.
                if data.lstrip(b'\xef\xbb\xbf \t\n\r')[:1] in (b'{', b'['):
                    raise ValueError('Saved draft contains malformed JSON')
                codec = codec or pinned_codec(config)
                decode_file(copied, out, codec=codec)
                method = 'pinned_native_codec'
            files.append({'name': name, 'source': str(source), 'source_sha256': before,
                          'snapshot': str(out), 'snapshot_sha256': sha256(out), 'method': method})
        if any(sha256(item['source']) != item['source_sha256'] for item in files):
            raise ValueError('Saved project changed while readback was being prepared')
        report = {'schema': 'aicut-native-readback-1', 'status': 'decoded_snapshot',
                  'editor_project': str(project), 'snapshot_project': str(target), 'files': files,
                  'codec': codec, 'originals_unchanged': True, 'semantic_validation': 'pending',
                  'executed_in_editor': False, 'native_export_verified': False}
        atomic_json(token / 'readback.json', report)
        return report
    except Exception as exc:
        atomic_json(token / 'failure.json', {'status': 'blocked', 'error': type(exc).__name__,
                                           'reason': str(exc), 'originals_written': False})
        raise


def validate_snapshot(report):
    for item in report['files']:
        if sha256(item['source']) != item['source_sha256'] or sha256(item['snapshot']) != item['snapshot_sha256']:
            raise ValueError('Readback source or decoded snapshot changed')
    if report.get('codec'):
        if sha256(report['codec']['dll']) != report['codec']['dll_sha256']:
            raise ValueError('Readback codec changed')
