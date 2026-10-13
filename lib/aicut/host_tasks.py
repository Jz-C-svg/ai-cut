"""Recoverable host checkpoints. UI observations are supplied by the host operator.

This module never clicks an editor or treats a file writer as native execution.
The host-task is authoritative; status repairs the two derived progress views.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

from .paths import ROOT, ffmpeg, ffprobe
from .production import atomic_json, file_lock, read_json, validate_draft


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def binding(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': digest(path)}


def _screenshot(path):
    from PIL import Image
    with Image.open(path) as shot:
        shot.verify()
    return binding(path)


def expected_media(artifact, require_audio=True):
    path = Path(artifact).resolve(strict=True)
    check = validate_draft(path.parent)
    data = read_json(path)
    canvas = data.get('canvas_config') or {}
    return {'duration': check['duration'], 'width': int(canvas['width']),
            'height': int(canvas['height']), 'fps': float(data['fps']),
            'require_audio': bool(require_audio)}


def verify_media(video, expected):
    """Check the actual MP4, including full decoding; no aesthetic verdict."""
    path = Path(video).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != '.mp4':
        raise ValueError('Expected a local MP4 file')
    before = binding(path)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    result = subprocess.run([ffprobe(), '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', str(path)], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', check=True, timeout=60,
                            creationflags=flags)
    data = json.loads(result.stdout)
    streams = data.get('streams') or []
    video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if not video_stream:
        raise ValueError('Export has no video stream')
    duration = float(data['format']['duration'])
    fps = float(Fraction(video_stream.get('avg_frame_rate', '0/1')))
    if not math.isfinite(duration) or duration <= 0 or abs(duration - expected['duration']) > .2:
        raise ValueError('Export duration differs from the task timeline')
    if (video_stream['width'], video_stream['height']) != (expected['width'], expected['height']):
        raise ValueError('Export dimensions differ from the task timeline')
    if not math.isfinite(fps) or fps <= 0 or abs(fps - expected['fps']) > .05:
        raise ValueError('Export frame rate differs from the task timeline')
    audio = any(s.get('codec_type') == 'audio' for s in streams)
    if expected['require_audio'] and not audio:
        raise ValueError('Required audio stream is missing')
    result = subprocess.run([ffmpeg(), '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
                             '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=min(3600, max(60, duration * 10 + 60)), creationflags=flags)
    if result.returncode:
        raise ValueError('Full export decode failed: ' + result.stderr[-500:])
    if digest(path) != before['sha256']:
        raise ValueError('Export changed while being checked')
    return {**before, 'duration': duration, 'width': video_stream['width'],
            'height': video_stream['height'], 'fps': fps, 'audio_present': audio,
            'full_decode_passed': True, 'visual_review': 'pending', 'ready_to_ship': False}


def create(path, *, task_id, project, sheet, prepared, made, editor_project=None,
           deliverable='draft', visible_operations=False):
    if deliverable not in {'draft', 'mp4'}:
        raise ValueError('Host deliverable must be draft or mp4')
    path = Path(path).resolve()
    if path.exists():
        raise ValueError('A host task already exists; resume it instead')
    artifact = Path(made['artifact']).resolve(strict=True)
    effective = artifact.parent / 'aicut.execution-sheet.json'
    steps = ['open_exact_project']
    if visible_operations:
        steps.append('visible_placement_and_edit')
    steps.append('verify_timeline_and_resources')
    if deliverable == 'mp4':
        steps += ['start_native_export', 'native_export', 'verify_export_media']
    output = path.parent / (project + '.native.mp4')
    if output.exists():
        raise ValueError('The reserved native output already exists')
    plan = read_json(sheet)
    if 'require_audio' in plan and not isinstance(plan['require_audio'], bool):
        raise ValueError('require_audio must be boolean')
    spec = {'task_id': task_id, 'software': 'jianying', 'project': project,
            'request_id': prepared['request_id'], 'request_sha256': prepared['request_sha256'],
            'sheet': binding(sheet), 'project_artifact': binding(artifact),
            'effective_sheet': binding(effective if effective.is_file() else sheet),
            'inputs': (prepared.get('inspection') or {}).get('input_versions') or [],
            'deliverable': deliverable, 'visible_operations': bool(visible_operations),
            'output': str(output), 'expected': expected_media(artifact, plan.get('require_audio', True)),
            'steps': steps}
    now = time.time()
    task = {'schema': 'aicut-host-task-2', 'spec': spec, 'spec_sha256': _spec_digest(spec),
            # Keep the original reader's project fields available to existing hosts.
            'software': 'jianying', 'project': project, 'project_artifact': str(artifact),
            'editor_project': str(Path(editor_project).resolve()) if editor_project else None,
            'created_at': now, 'updated_at': now, 'status': 'pending_host_execution',
            'steps': {name: {'state': 'pending', 'attempts': 0} for name in steps},
            'events': [], 'executed_in_editor': False, 'visible_placement_verified': False,
            'native_export_verified': False, 'ready_to_ship': False, 'visual_review': 'pending'}
    atomic_json(path, task)
    return task


def _spec_digest(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _validate(task):
    if task.get('schema') != 'aicut-host-task-2':
        raise ValueError('Legacy handoff has no bound checkpoints; keep it pending, do not infer completion')
    if _spec_digest(task['spec']) != task.get('spec_sha256'):
        raise ValueError('Host task specification changed')
    for item in [task['spec'][key] for key in ('sheet', 'effective_sheet', 'project_artifact')] + task['spec']['inputs']:
        if digest(item['path']) != item['sha256']:
            raise ValueError('Bound plan, project or input changed; prepare a new task: ' + item['path'])
    for step in task['steps'].values():
        for item in step.get('evidence', {}).get('screenshots', []):
            if digest(item['path']) != item['sha256']:
                raise ValueError('Checkpoint screenshot changed or disappeared')
    media = task.get('media_validation')
    if media and digest(media['path']) != media['sha256']:
        raise ValueError('Verified export changed or disappeared')
    timeline = task['steps']['verify_timeline_and_resources'].get('evidence', {}).get('timeline')
    if timeline and timeline.get('project_file'):
        item = timeline['project_file']
        if digest(item['path']) != item['sha256']:
            # An editor may rewrite bookkeeping during autosave. Accept only a
            # fresh semantic audit of the saved copy, never the untouched writer output.
            checked = _timeline(task)
            task['steps']['verify_timeline_and_resources']['evidence']['timeline'] = checked
            task['events'].append({'step': 'verify_timeline_and_resources',
                                   'state': 'saved_timeline_rechecked', 'at': time.time()})
        elif timeline.get('readback'):
            from .native_draft import validate_snapshot
            validate_snapshot(timeline['readback'])


def _next(task):
    return next((name for name in task['spec']['steps']
                 if task['steps'][name]['state'] != 'succeeded'), None)


def _derive(task):
    task['executed_in_editor'] = task['steps']['verify_timeline_and_resources']['state'] == 'succeeded'
    task['visible_placement_verified'] = task['steps'].get('visible_placement_and_edit', {}).get('state') == 'succeeded'
    task['native_export_verified'] = task['steps'].get('verify_export_media', {}).get('state') == 'succeeded'
    next_step = _next(task)
    task['status'] = ('blocked' if next_step and task['steps'][next_step]['state'] == 'blocked' else
                      'pending_host_execution' if next_step == 'open_exact_project' else
                      'host_execution_in_progress' if next_step else
                      'native_export_verified_pending_visual_review' if task['native_export_verified'] else
                      'host_project_verified_pending_visual_review')


def _sync(path, task):
    """Derived progress only. Never mutate MakerAPI's approval or result."""
    fields = {k: task[k] for k in ('status', 'executed_in_editor', 'visible_placement_verified',
                                  'native_export_verified', 'ready_to_ship', 'visual_review')}
    report = path.parent / 'autopilot.json'
    if report.is_file():
        data = read_json(report)
        if data.get('task_id') != task['spec']['task_id']:
            raise ValueError('Autopilot report belongs to another task')
        data.setdefault('production_status', (data.get('production') or {}).get('status', data.get('status')))
        data.update(fields, ok=task['status'] != 'blocked', host_task=str(path),
                    host_next_step=_next(task), host_updated_at=task['updated_at'])
        if task.get('media_validation'):
            data['native_output'] = task['media_validation']['path']
        atomic_json(report, data)
    status = path.parent / 'status.json'
    if status.is_file():
        data = read_json(status)
        if data.get('id') != task['spec']['task_id']:
            raise ValueError('Job state belongs to another task')
        if data.get('finished_at'):
            data.setdefault('generation_finished_at', data.pop('finished_at'))
        data.update(state=task['status'], updated_at=task['updated_at'])
        data.setdefault('artifacts', {})['host_task'] = str(path)
        atomic_json(status, data)


def _validate_or_block(path, task):
    if task.get('schema') != 'aicut-host-task-2':
        _validate(task)
    try:
        _validate(task)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        task.update(status='blocked', validation_error=str(exc), updated_at=time.time(),
                    executed_in_editor=False, native_export_verified=False,
                    visible_placement_verified=False, ready_to_ship=False)
        atomic_json(path, task)
        _sync(path, task)
        raise


def status(path):
    path = Path(path).resolve(strict=True)
    with file_lock(path.with_suffix('.lock'), timeout=2):
        task = read_json(path)
        _validate_or_block(path, task)
        task.pop('validation_error', None)
        _derive(task)
        task['updated_at'] = time.time()
        atomic_json(path, task)
        _sync(path, task)
        return {**task, 'next_step': _next(task), 'host_task': str(path),
                'note': 'Resume the next step through computer-use; this command performs no UI actions'}


def _timeline(task):
    folder = Path(task.get('editor_project') or '')
    if not folder.is_absolute() or folder.name != task['project']:
        raise ValueError('The exact editor project directory has not been observed')
    if folder.resolve() == Path(task['spec']['project_artifact']['path']).parent:
        raise ValueError('The original generated directory is not an independent saved editor project')
    audit_folder, readback = folder, None
    try:
        validate_draft(folder)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        from .native_draft import snapshot
        try:
            readback = snapshot(folder)
            audit_folder = Path(readback['snapshot_project'])
        except (OSError, ValueError, ImportError, AttributeError) as codec_error:
            raise ValueError('encrypted_saved_draft_needs_host_verification: saved editor readback failed; '
                             'do not substitute the generated draft. ' + str(codec_error)) from exc
    scripts = str(ROOT / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import build_draft
    sheet = read_json(task['spec']['effective_sheet']['path'])
    report = build_draft.runtime_asset_report(sheet, audit_folder)
    if readback:
        readback['semantic_validation'] = report['status']
        readback['execution_audit'] = report
        atomic_json(audit_folder.parent / 'readback.json', readback)
    if report['status'] != 'complete':
        detail = ('; decoded readback report: ' + str(audit_folder.parent / 'readback.json')) if readback else ''
        raise ValueError('Saved editor timeline or resources differ from the production plan' + detail)
    validate_draft(audit_folder)
    if not read_json(audit_folder / 'draft_content.json').get('fps'):
        raise ValueError('Saved editor draft does not persist fps; export settings need independent verification')
    expected = expected_media(audit_folder / 'draft_content.json', task['spec']['expected']['require_audio'])
    if expected != task['spec']['expected']:
        raise ValueError('Saved editor canvas, fps or duration differs from the task')
    if readback:
        from .native_draft import validate_snapshot
        validate_snapshot(readback)
    return {'project_file': binding(folder / 'draft_content.json'), 'execution_audit': report,
            **({'readback': readback} if readback else {})}


def _failure(task, step, reason):
    now = time.time()
    task['steps'][step].update(state='blocked', attempts=task['steps'][step]['attempts'] + 1,
                               error=reason, updated_at=now)
    task['events'].append({'step': step, 'state': 'blocked', 'at': now, 'error': reason})
    task.update(status='blocked', updated_at=now)


def record(path, step, evidence=None, *, error=None):
    """Register an observed UI step, or machine-check the reserved native output.

    Screenshot/observation fields are host attestations, not screenshot recognition.
    Repeated identical successful checkpoints are idempotent.
    """
    path = Path(path).resolve(strict=True)
    with file_lock(path.with_suffix('.lock'), timeout=2):
        task = read_json(path)
        _validate_or_block(path, task)
        task.pop('validation_error', None)
        _derive(task)
        if step not in task['steps']:
            raise ValueError('Unknown host step')
        current = task['steps'][step]
        if current['state'] == 'succeeded':
            if error:
                raise ValueError('Successful checkpoint cannot be overwritten by a failure')
            atomic_json(path, task)
            _sync(path, task)
            return {**task, 'next_step': _next(task), 'reused': True}
        if step != _next(task):
            raise ValueError('Complete the preceding host step first')
        if current['attempts'] >= 2:
            raise ValueError('Two attempts failed; preserve the task and diagnose before retrying')
        now = time.time()
        if error:
            if not isinstance(error, str) or not error.strip():
                raise ValueError('A failure reason is required')
            _failure(task, step, error)
        else:
            if step != 'verify_export_media':
                evidence = evidence or {}
                if evidence.get('project') != task['project'] or evidence.get('channel') != 'computer-use':
                    raise ValueError('UI evidence must identify this project and the computer-use channel')
                if not isinstance(evidence.get('observation'), str) or not evidence['observation'].strip():
                    raise ValueError('Describe the actual observed UI result')
                shots = evidence.get('screenshots')
                if not isinstance(shots, list) or not shots:
                    raise ValueError('At least one captured UI screenshot is required')
                if step in ('start_native_export', 'native_export') and (
                        Path(evidence.get('output_path') or '').resolve() != Path(task['spec']['output'])):
                    raise ValueError('Observed export path must match the reserved task output')
                if step == 'native_export' and evidence.get('export_success_observed') is not True:
                    raise ValueError('Native export requires an observed success result in the editor')
            try:
                if step == 'verify_export_media':
                    _timeline(task)
                    output = Path(task['spec']['output'])
                    started = task.get('export_started_at')
                    if not started or output.stat().st_mtime < started - 1:
                        raise ValueError('Export predates the recorded native export attempt')
                    checked = {'media': verify_media(output, task['spec']['expected'])}
                    task['media_validation'] = checked['media']
                else:
                    checked = {**evidence, 'screenshots': [_screenshot(shot) for shot in shots]}
                    if step == 'open_exact_project':
                        folder = Path(evidence.get('editor_project') or task.get('editor_project') or '')
                        if not folder.is_absolute() or folder.name != task['project'] or not folder.is_dir():
                            raise ValueError('Observed editor directory must match the exact project name')
                        if folder.resolve() == Path(task['spec']['project_artifact']['path']).parent:
                            raise ValueError('The original generated directory is not an independent saved editor project')
                        task['editor_project'] = str(folder.resolve())
                    elif step == 'verify_timeline_and_resources':
                        checked['timeline'] = _timeline(task)
                    elif step == 'start_native_export':
                        if Path(task['spec']['output']).exists():
                            raise ValueError('Reserved native output exists before export; do not overwrite it')
                        task['export_started_at'] = now
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                _failure(task, step, f'{type(exc).__name__}: {exc}')
                atomic_json(path, task)
                _sync(path, task)
                raise
            current.update(state='succeeded', attempts=current['attempts'] + 1,
                           evidence=checked, updated_at=now)
            current.pop('error', None)
            task['events'].append({'step': step, 'state': 'succeeded', 'at': now})
            _derive(task)
        task['updated_at'] = now
        atomic_json(path, task)
        _sync(path, task)
        return {**task, 'next_step': _next(task)}
