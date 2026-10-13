"""One user brief -> the existing brain pipeline -> immutable production request.

This application coordinator is deliberately outside the model tool catalog.
execute=True represents the caller's authorization to make a NEW local output.
It does not enable native UI export or approve unrelated/external operations.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from .jobs import Job, Cancelled
from .maker_api import MakerAPI
from .paths import ROOT, config, draft_root
from . import host_tasks
from .production import atomic_json, project_name, read_json


def planning_command(material, goal, name, task_id, style, timeout):
    return [sys.executable, str(ROOT / 'scripts' / 'pipeline.py'),
            '--video', str(material), '--goal', goal, '--name', name,
            '--style', style, '--smart', '--intent', '--no-build',
            '--task-id', task_id, '--stage-timeout', str(timeout)]


def check_brain(sheet):
    health = sheet.get('production_health') or {}
    for prefix in ('intent', 'understanding'):
        if health.get(prefix + '_requested') and not health.get(prefix + '_available'):
            raise ValueError(f'{prefix} model did not complete; rule fallback is not autonomous understanding')
    if health.get('intent_degraded'):
        raise ValueError('Intent model degraded; preserve the plan for review')


def run(*, goal, material=None, sheet=None, mode='draft', execute=False,
        copy_to_editor=False, style='koubo-zhishi', timeout=21600,
        host_deliverable='draft', visible_operations=False):
    if bool(material) == bool(sheet):
        raise ValueError('Supply exactly one material or existing sheet')
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError('A user brief is required')
    if mode not in ('draft', 'mp4'):
        raise ValueError('Only draft and portable MP4 are supported here; native export needs a host executor')
    if copy_to_editor and mode != 'draft':
        raise ValueError('Copying to Jianying requires draft mode')
    if host_deliverable not in ('draft', 'mp4'):
        raise ValueError('Host deliverable must be draft or mp4')
    if mode != 'draft' and (host_deliverable != 'draft' or visible_operations):
        raise ValueError('Host options cannot be used with portable MP4')
    source = Path(material or sheet).resolve(strict=True)
    if not source.is_file():
        raise ValueError('The current pipeline accepts one video file per job')
    # Keep the current local-only project choice. Do not switch providers or
    # activate a paid cloud fallback merely because a local model is unavailable.
    if material and config().get('ai_router', {}).get('global_paid_api_enabled', True):
        raise ValueError('This local autonomous workflow requires paid API routing disabled')
    ident = 'autopilot-' + uuid.uuid4().hex
    name = project_name('AI-CUT-' + ident[-16:])
    job = Job(ROOT, ident, timeout)
    result = {'schema': 'aicut-autopilot-1', 'task_id': ident, 'goal': goal,
              'status': 'planning', 'ok': False, 'ready_to_ship': False,
              'executed_in_editor': False, 'native_export_verified': False,
              'plan_origin': 'existing_sheet' if sheet else 'brain_pipeline',
              'authorization': {'local_make_requested': bool(execute), 'mode': mode,
                                'copy_to_editor': bool(copy_to_editor),
                                'host_deliverable': host_deliverable,
                                'visible_operations': bool(visible_operations)}}
    report_path = job.directory / 'autopilot.json'
    result['report_path'] = str(report_path)
    atomic_json(report_path, result)
    try:
        if material:
            pipeline_id = ident + '-brain'
            job.data['artifacts']['brain_task_id'] = pipeline_id
            job.save()
            job.stage('AI CUT 自主理解和规划', lambda: job.command(
                planning_command(source, goal, name, pipeline_id, style, timeout), ROOT))
            state = read_json(ROOT / 'work' / 'tasks' / pipeline_id / 'status.json')
            if state.get('state') != 'planned':
                raise ValueError(state.get('error') or 'Brain pipeline did not finish this plan')
            if state.get('warnings'):
                raise ValueError('Brain pipeline needs review: ' + '; '.join(state['warnings']))
            current = (state.get('artifacts') or {}).get('working_sheet')
            if not current:
                raise ValueError('Brain pipeline returned no current decision sheet')
            data = read_json(current)
            result['brain_task_id'] = pipeline_id
        else:
            data = read_json(source)
        check_brain(data)
        # A fresh owned project prevents overwriting a user's existing project.
        data['project'] = name
        owned_sheet = job.directory / 'production.sheet.json'
        atomic_json(owned_sheet, data)
        engine = 'project' if mode == 'draft' else 'portable'
        output = str(job.directory / (name + '.mp4')) if mode == 'mp4' else None
        api = MakerAPI()
        prepared = job.stage('制作能力和方案检查', lambda: api.prepare(
            owned_sheet, 'jianying', engine, output, copy_to_editor))
        result['prepared'] = prepared
        result['sheet'] = str(owned_sheet)
        if not prepared['ok']:
            raise ValueError(prepared.get('error') or 'Production preflight failed')
        if not execute:
            result.update(ok=True, status='awaiting_confirmation')
        else:
            # This is a scoped application authorization for this invocation,
            # not a new model argument or a persistent blanket approval.
            atomic_json(job.directory / 'authorization.json', {
                'source': 'caller_requested_local_autonomous_make',
                'goal': goal, 'request_id': prepared['request_id'],
                'request_sha256': prepared['request_sha256'],
                'selection': prepared['selection']})
            made = job.stage('调用共同制作接口', lambda: api.execute(
                prepared['request_id'], approved_request_sha256=prepared['request_sha256']))
            result['production'] = made
            if not made['ok']:
                raise ValueError(made.get('error') or 'Production failed')
            result.update(ok=True, status=made['status'], artifact=made['artifact'])
            if mode == 'draft':
                host_path = job.directory / 'host-task.json'
                host_tasks.create(host_path, task_id=ident, project=name, sheet=owned_sheet,
                                  prepared=prepared, made=made,
                                  editor_project=draft_root() / name if copy_to_editor else None,
                                  deliverable=host_deliverable, visible_operations=visible_operations)
                result['host_task'] = str(host_path)
                result['production_status'] = made['status']
                result['status'] = 'pending_host_execution'
                job.data['artifacts']['host_task'] = str(host_path)
        job.finish(result['status'])
    except (Exception, KeyboardInterrupt) as exc:
        result.update(status='cancelled' if isinstance(exc, (Cancelled, KeyboardInterrupt)) else 'blocked',
                      ok=False, error=f'{type(exc).__name__}: {exc}')
        job.finish(result['status'], result['error'])
    atomic_json(report_path, result)
    if result.get('host_task') and result['ok']:
        host_tasks.status(result['host_task'])
        result = read_json(report_path)
    return result
