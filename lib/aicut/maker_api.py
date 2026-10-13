"""Structured production tools for the brain; rendering stays in the shared writer.

Model arguments never contain an approval token. The application supplies approvals
separately after the user reviews the plan and the selected production destination.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

from .paths import ROOT, WORK
from .production import atomic_json, file_lock


def _delivery():
    scripts = str(ROOT / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import deliver_edit
    return deliver_edit


def _digest(data):
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _input_versions(plan):
    """Bind existing local file references in the decision sheet to this approval."""
    found = {}
    def visit(value):
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value:
            path = Path(value)
            if not path.is_absolute():
                path = ROOT / path
            try:
                exists = path.is_file()
            except (OSError, ValueError):
                return
            if exists:
                path = path.resolve(strict=True)
                found[str(path)] = _file_digest(path)
    visit(plan)
    return [{'path': path, 'sha256': digest} for path, digest in sorted(found.items())]


class MakerAPI:
    """Prepare an immutable request, then execute it with application approval."""

    def __init__(self, request_root=None):
        self.root = Path(request_root or WORK / 'maker-api').resolve()

    def prepare(self, sheet, software='jianying', engine='project', output=None, publish=False):
        path = Path(sheet).resolve(strict=True)
        if not isinstance(publish, bool):
            raise ValueError('publish must be a boolean')
        if publish and (software != 'jianying' or engine != 'project'):
            raise ValueError('Only Jianying project delivery can copy into the editor library')
        if output and engine != 'portable':
            raise ValueError('output is only used by portable MP4 delivery')
        target = str(Path(output).resolve()) if output else None
        if target and Path(target).suffix.lower() != '.mp4':
            raise ValueError('Output must be an MP4 file')
        inspected = _delivery().inspect_plan(path, software, engine)
        if not inspected['ready']:
            return {'ok': False, 'status': 'blocked', 'inspection': inspected,
                    'error': '; '.join(inspected['blockers'])}
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != inspected['sheet_sha256']:
            raise ValueError('Plan changed during inspection; inspect again')
        inputs = _input_versions(inspected['plan'])
        inspected['input_versions'] = inputs
        request_id = uuid.uuid4().hex
        folder = self.root / request_id
        folder.mkdir(parents=True)
        (folder / 'plan.sheet.json').write_bytes(raw)
        request = {'schema': 'aicut-maker-request-1', 'request_id': request_id,
                   'sheet': str(path), 'sheet_sha256': inspected['sheet_sha256'],
                   'software': software, 'engine': engine, 'output': target, 'publish': publish,
                   'inputs': inputs}
        request_hash = _digest(request)
        atomic_json(folder / 'request.json', {'request': request, 'request_sha256': request_hash})
        return {'ok': True, 'status': 'awaiting_confirmation', 'request_id': request_id,
                'request_sha256': request_hash, 'inspection': inspected,
                'selection': {'software': software, 'engine': engine, 'output': target,
                              'publish': publish},
                'note': 'Review the exact plan and destination before approving this request'}

    def _folder(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{32}', request_id):
            raise ValueError('Invalid production request ID')
        folder = (self.root / request_id).resolve(strict=True)
        if folder.parent != self.root:
            raise ValueError('Production request escaped its directory')
        return folder

    def execute(self, request_id, *, approved_request_sha256):
        folder = self._folder(request_id)
        with file_lock(folder / 'execute.lock', timeout=1):
            saved = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
            request = saved['request']
            digest = _digest(request)
            if (request.get('schema') != 'aicut-maker-request-1'
                    or request.get('request_id') != request_id
                    or not isinstance(request.get('inputs'), list)
                    or saved.get('request_sha256') != digest
                    or approved_request_sha256 != digest):
                raise ValueError('Production request has not been approved or was changed')
            snapshot = folder / 'plan.sheet.json'
            for path in (snapshot, Path(request['sheet'])):
                if hashlib.sha256(path.read_bytes()).hexdigest() != request['sheet_sha256']:
                    raise ValueError('Plan changed after inspection; prepare and approve again')
            for item in request.get('inputs', []):
                path = Path(item['path'])
                if not path.is_file() or _file_digest(path) != item['sha256']:
                    raise ValueError(f'Input changed after inspection; prepare and approve again: {path}')
            result_path = folder / 'result.json'
            if result_path.exists():
                previous = json.loads(result_path.read_text(encoding='utf-8'))
                if previous.get('ok'):
                    artifact = Path(previous['artifact'])
                    if not artifact.is_file() or _file_digest(artifact) != previous['artifact_sha256']:
                        raise ValueError('Previous output changed or disappeared; prepare a new request')
                    return {**previous, 'reused': True}
            report_path, report = _delivery().deliver(
                snapshot, request['software'], request['engine'], request['output'],
                approved_sha256=request['sheet_sha256'], publish=request['publish'],
                report_out=folder / 'delivery.json')
            success = report['status'] in {'project_generated_pending_host_verification',
                                            'rendered_pending_visual_review'}
            result = {'ok': success, 'status': report['status'], 'request_id': request_id,
                      'report_path': str(report_path), 'delivery': report,
                      'ready_to_ship': False}
            artifact = report['artifacts'].get('video') or report['artifacts'].get('project')
            if success and artifact:
                result['artifact'] = artifact
                with Path(artifact).open('rb') as stream:
                    result['artifact_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
            if not success:
                result['error'] = report.get('error', 'Production did not finish')
            atomic_json(result_path, result)
            return result

    def call_tool(self, name, arguments, *, approvals=None):
        """Dispatch only declared tools; approvals come from the app, never the model."""
        if not isinstance(arguments, dict):
            raise ValueError('Tool arguments must be a JSON object')
        if name == 'inspect_video_plan':
            if set(arguments) - {'sheet', 'software', 'engine', 'output', 'publish'}:
                raise ValueError('Unknown inspection arguments')
            if not arguments.get('sheet'):
                raise ValueError('sheet is required')
            return self.prepare(**arguments)
        if name == 'make_video':
            if set(arguments) != {'request_id'}:
                raise ValueError('make_video accepts request_id only; approval is supplied by the application')
            request_id = arguments['request_id']
            approved = (approvals or {}).get(request_id)
            if not approved:
                raise ValueError('Waiting for user confirmation of this production request')
            return self.execute(request_id, approved_request_sha256=approved)
        raise ValueError(f'Unknown production tool: {name}')


def tool_definitions():
    """Function schemas suitable for a model tool catalog; no arbitrary code execution."""
    return [
        {'type': 'function', 'function': {
            'name': 'inspect_video_plan',
            'description': 'Check an AI CUT decision sheet and prepare a user-reviewable video production request.',
            'parameters': {'type': 'object', 'additionalProperties': False,
                           'required': ['sheet', 'software', 'engine'], 'properties': {
                               'sheet': {'type': 'string'},
                               'software': {'type': 'string', 'enum': ['jianying', 'capcut', 'premiere', 'davinci', 'finalcut', 'vegas', 'aftereffects']},
                               'engine': {'type': 'string', 'enum': ['project', 'portable', 'native']},
                               'output': {'type': 'string'}, 'publish': {'type': 'boolean'}}}}},
        {'type': 'function', 'function': {
            'name': 'make_video',
            'description': 'Execute a prepared request only after the application records user approval. Unsupported native export is blocked.',
            'parameters': {'type': 'object', 'additionalProperties': False,
                           'required': ['request_id'], 'properties': {'request_id': {'type': 'string'}}}}},
    ]
