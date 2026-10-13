"""Unified production entry: prepare -> transcript -> plan -> build -> review -> delivery.

No implicit publishing; --publish means copying into Jianying, not uploading.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))
from aicut.jobs import Job, Cancelled
from aicut.production import read_json, atomic_json, project_name, validate_draft, file_lock


def validate_transcript(path, duration):
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get('segments'), list):
        raise ValueError('逐字稿缺少有效 segments 列表')
    previous_start = -1.0
    for seg in data['segments']:
        if not isinstance(seg, dict) or not isinstance(seg.get('text'), str) or not seg['text'].strip():
            raise ValueError('逐字稿包含空文本或无效片段')
        try:
            a, b = float(seg['start']), float(seg['end'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('逐字稿片段缺少有效时间码') from exc
        if (not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a
                or b > duration + .5 or a < previous_start):
            raise ValueError('逐字稿时间超出素材范围')
        previous_start = a
    stated_duration = data.get('duration')
    if stated_duration is not None:
        try:
            stated_duration = float(stated_duration)
        except (TypeError, ValueError) as exc:
            raise ValueError('逐字稿声明时长无效') from exc
        if not math.isfinite(stated_duration) or abs(stated_duration - duration) > max(.75, duration * .01):
            raise ValueError('逐字稿与素材时长不匹配')
    return data


def transcript_cache_matches(path, audio_path, asr, vocabulary=''):
    """Reuse only a transcript produced from these exact audio bytes and settings."""
    try:
        data = read_json(path)
        meta = data.get('_asr') or {}
        digest = hashlib.sha256()
        with Path(audio_path).open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return (meta.get('model') == asr.get('model')
                and meta.get('language') == asr.get('language')
                and meta.get('requested_device') == asr.get('device')
                and meta.get('requested_compute_type') == asr.get('compute_type')
                and meta.get('gpu_compute_type') == asr.get('gpu_compute_type', 'float16')
                and int(meta.get('beam_size', -1)) == int(os.environ.get('WHISPER_BEAM', asr.get('beam_size', 5)))
                and meta.get('vad') == bool(asr.get('vad'))
                and meta.get('word_timestamps') == (os.environ.get('WHISPER_WORD_TS', '0') == '1')
                and meta.get('vocabulary') == vocabulary[:500]
                and meta.get('source_sha256') == digest.hexdigest())
    except (OSError, ValueError, TypeError, KeyError):
        return False


def execute(args, forwarded, job):
    import preprocess
    import build_draft
    cfg = preprocess.load_config()
    scripts = ROOT / 'scripts'
    def command(script, *values):
        return job.command([sys.executable, str(scripts / script), *map(str, values)], ROOT)

    source = Path(args.video).resolve(strict=True)
    if not source.is_file():
        raise ValueError('流水线一次处理一个视频文件；批量入口会逐个建立任务')
    # Reuse only manifest-owned prepared media; an arbitrary file is always versioned.
    manifest_path = ROOT / 'work' / 'manifest.json'
    existing = read_json(manifest_path).get('items', []) if manifest_path.exists() else []
    matched = next((i for i in existing if Path(i['video']).resolve() == source), None)
    original = Path(matched['source']).resolve() if matched else source
    def prepare():
        command('preprocess.py', original)
        manifest = read_json(manifest_path)
        return next(i for i in manifest['items'] if Path(i['source']).resolve() == original)
    item = job.stage('素材检查与预处理', prepare)
    job.data['artifacts']['material'] = item
    video = Path(item['video'])
    transcript = Path(args.transcript).resolve() if args.transcript else ROOT / '02-transcript' / (item['name'] + '.json')

    def transcribe():
        if args.transcript:
            if not transcript.is_file():
                raise FileNotFoundError(transcript)
        elif (not transcript.is_file() or not item.get('audio')
              or not transcript_cache_matches(transcript, item['audio'], cfg['asr'], args.vocabulary or '')):
            if not item.get('has_audio'):
                atomic_json(transcript, {'segments': [], 'duration': item['duration'], 'silent': True})
            elif args.skip_transcribe:
                raise ValueError('要求跳过转写，但当前素材版本没有匹配 ASR 参数与音轨指纹的逐字稿')
            else:
                # Keep the configured ASR model as the single source of truth. A
                # hard-coded small model here silently defeated quality experiments.
                cmd = ['transcribe.py', item['audio']]
                if args.vocabulary:
                    cmd += ['--vocabulary', args.vocabulary]
                command(*cmd)
        data = validate_transcript(transcript, item['duration'])
        if item.get('has_audio') and not data.get('segments'):
            # Empty ASR must not silently become a visual-only edit: it can be a
            # failed decode, unsupported language, or VAD over-filtering.
            job.data['warnings'].append('检测到音轨但 ASR 未识别到任何语句；需检查音轨/语言/VAD 后重试')
            raise ValueError('有音轨但逐字稿为空，停止自动剪辑以避免遗漏内容')
        return str(transcript)
    job.data['artifacts']['transcript'] = job.stage('语音转写', transcribe)
    framewise_status = job.directory / 'framewise-status.json'
    job.stage('全量逐帧画面理解', lambda: command(
        'scan_every_frame.py', '--video', original, '--status-out', framewise_status))
    framewise_report = read_json(framewise_status)
    _require(framewise_report.get('complete') and framewise_report.get('state') == 'complete',
             '逐帧视觉扫描未覆盖全部解码帧，停止自动剪辑')
    job.data['artifacts']['framewise_visual'] = str(framewise_status)
    semantic_path = ROOT / 'work' / ('semantic-' + video.stem + '.json')
    if args.semantic or args.analyze_only:
        code = ('import sys; from aicut import semantic_profile; '
                'from aicut.production import atomic_json; '
                'r=semantic_profile.build(sys.argv[1], transcript_path=sys.argv[2], use_cloud=False, force=True); '
                'atomic_json(sys.argv[3],r); '
                'sys.exit(0 if r.get("主角") and r.get("关键信息点") else 4)')
        cache_marker = semantic_path.with_suffix('.input.json')
        context_key = hashlib.sha256(transcript.read_bytes() + json.dumps(item.get('source_version'), sort_keys=True).encode()).hexdigest()
        def analyze():
            if semantic_path.is_file() and cache_marker.is_file() and read_json(cache_marker).get('key') == context_key:
                doc = read_json(semantic_path)
                if doc.get('主角') and doc.get('关键信息点'):
                    return
            job.command([sys.executable, '-c', code, str(video), str(transcript), str(semantic_path)], ROOT)
            atomic_json(cache_marker, {'key': context_key})
        job.stage('语义分析', analyze)
        job.data['artifacts']['semantic'] = str(semantic_path)
    if args.analyze_only:
        return 'completed'

    name = project_name(args.name or (item.get('label', source.stem)[:55] + '-' + job.data['id'][-12:]))
    sheet_path = ROOT / '03-scripts' / (name + '.sheet.json')
    with file_lock(ROOT / 'work' / 'locks' / ('production-' + name + '.lock'), timeout=1):
        if sheet_path.exists():
            import shutil
            shutil.copy2(sheet_path, job.directory / 'previous.sheet.json')
        plan_args = ['--video', str(video), '--transcript', str(transcript), '--name', name, '--no-build', *forwarded]
        plan_args += ['--framewise-status', str(framewise_status)]
        if args.semantic:
            plan_args += ['--semantic', '--semantic-path', str(semantic_path)]
        job.stage('学习库检索与剪辑决策', lambda: command('auto_edit.py', *plan_args))
        sheet = read_json(sheet_path)
        sheet_snapshot = job.directory / 'decision.sheet.json'
        atomic_json(sheet_snapshot, sheet)
        job.data['artifacts']['sheet'] = str(sheet_snapshot)
        job.data['artifacts']['working_sheet'] = str(sheet_path)
        problems = build_draft.quality_gates(sheet)
        job.stage('生成前质量门', lambda: _require(not problems, '; '.join(problems)))
        if args.no_build:
            return 'planned'
        if sheet.get('requires_plan_confirmation'):
            job.data['artifacts']['plan_sha256'] = hashlib.sha256(sheet_path.read_bytes()).hexdigest()
            job.data['warnings'].append('等待用户确认当前版本决策单，再调用 build_draft.py 生成草稿')
            return 'planned'
        job.stage('生成可恢复草稿', lambda: command('build_draft.py', sheet_path))
        draft = ROOT / '06-drafts' / name
        job.data['artifacts']['draft'] = str(draft)
        runtime_assets = draft / 'aicut-runtime-assets.json'
        if runtime_assets.is_file():
            job.data['artifacts']['runtime_assets'] = str(runtime_assets)
            asset_report = read_json(runtime_assets)
            if asset_report.get('status') != 'complete':
                job.data['warnings'].append('部分花字/特效未实时写入剪映草稿；查看 aicut-runtime-assets.json')
                return 'needs_review'
        else:
            raise RuntimeError('草稿已生成，但缺少实时花字/特效调用报告')
        stages = ((sheet.get('recognition_pipeline') or {}).get('stages') or [])
        if stages and stages[0].get('status') in {'failed', 'needs_review'}:
            job.data['warnings'].append('视觉识别失败或可信度不足；花字与剪辑仅基于 ASR/风格执行，需人工核对画面')
        if args.copilot:
            # Agent runs in a separate child so timeout/cancel applies to its model and tools too.
            code = ('import json,sys; from pathlib import Path; from aicut.agent import Agent; '
                    'from aicut.production import atomic_json; '
                    'r=Agent(draft=Path(sys.argv[1]),goal=sys.argv[2],copilot_method=True,use_rules=True,max_iterations=2,task=sys.argv[3]).run(); '
                    'atomic_json(Path(sys.argv[4]),r)')
            job.stage('Copilot 复审', lambda: job.command([sys.executable, '-c', code, str(draft),
                      '复审并完善：' + (sheet.get('制作目标') or name), name+'-复审', str(job.directory / 'review.json')], ROOT))
            report = read_json(job.directory / 'review.json')
            if not report.get('ok') or not (report.get('review') or {}).get('ready_to_ship'):
                job.data['warnings'].append('Copilot 未通过，草稿已保留')
                return 'needs_review'
        # Copilot can edit the draft in place. Recompute the decoration report
        # from the final draft before snapshotting or publishing so the report
        # cannot describe an earlier, better version.
        asset_report = build_draft.runtime_asset_report(sheet, draft)
        atomic_json(runtime_assets, asset_report)
        if asset_report.get('status') != 'complete':
            job.data['warnings'].append('复审后的草稿未通过花字/特效回读校验；草稿已保留')
            return 'needs_review'
        validation = job.stage('草稿回读校验', lambda: validate_draft(draft))
        atomic_json(job.directory / 'validation.json', validation)
        atomic_json(job.directory / 'final-draft.json', read_json(draft / 'draft_content.json'))
        job.data['artifacts']['final_draft_snapshot'] = str(job.directory / 'final-draft.json')
        intent_path = sheet.get('intent_path')
        health = sheet.get('production_health') or {}
        if health.get('intent_requested') and not health.get('intent_available'):
            job.data['warnings'].append('意图模型未产出决策，需要人工复审后交付')
        if health.get('understanding_requested') and not health.get('understanding_available'):
            job.data['warnings'].append('智能剪辑识别未完成，需要人工复审后交付')
        if intent_path and (read_json(intent_path).get('_meta') or {}).get('degraded'):
            job.data['warnings'].append('意图模型降级，需要人工复审后交付')
        if job.data['warnings']:
            return 'needs_review'
        if args.export_mp4:
            if args.copilot:
                raise ValueError('Copilot 已修改草稿，请从剪映导出；决策单渲染无法保证与复审版本一致')
            out = ROOT / '07-output' / (name + '.mp4')
            job.stage('基础 MP4 导出', lambda: command('export_video.py', '--sheet', sheet_path, '--out', out))
            job.data['artifacts']['mp4'] = str(out)
        if args.publish and not args.no_publish:
            job.stage('复制复审后草稿到剪映', lambda: build_draft.publish(draft, cfg))
            job.data['artifacts']['published'] = str(Path(cfg['draft_root']) / name)
    return 'completed'


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument('--video')
    ap.add_argument('--name')
    ap.add_argument('--transcript')
    ap.add_argument('--vocabulary', default='', help='ASR 领域词提示；只用于识别提示，不自动替换文本')
    ap.add_argument('--skip-transcribe', action='store_true')
    ap.add_argument('--analyze-only', action='store_true')
    ap.add_argument('--semantic', action='store_true', default=True)
    ap.add_argument('--no-semantic', action='store_false', dest='semantic', help='明确关闭视觉语义分析，保留转写与学习库决策')
    ap.add_argument('--no-build', action='store_true')
    ap.add_argument('--publish', action='store_true')
    ap.add_argument('--no-publish', action='store_true')
    ap.add_argument('--copilot', action='store_true')
    ap.add_argument('--export-mp4', action='store_true')
    ap.add_argument('--stage-timeout', type=float, default=21600,
                    help='单任务硬超时秒数（质量优先默认 6 小时；随时可取消）')
    ap.add_argument('--task-id', default=uuid.uuid4().hex)
    ap.add_argument('--cancel', help='待取消任务 id')
    ap.add_argument('--feedback-task', help='为已完成的任务导入复盘数据')
    ap.add_argument('--metrics-data', help='用户自己的后台导出 CSV/XLSX')
    ap.add_argument('--match-column')
    ap.add_argument('--match-value')
    ap.add_argument('--note')
    ap.add_argument('--score', type=float)
    args, forwarded = ap.parse_known_args(argv)
    if args.cancel:
        task = ROOT / 'work' / 'tasks' / project_name(args.cancel)
        if not (task / 'status.json').is_file():
            ap.error('任务不存在')
        (task / 'cancel').touch()
        return 0
    if not args.video and not args.feedback_task:
        ap.error('需要 --video')
    if not math.isfinite(args.stage_timeout) or args.stage_timeout <= 0:
        ap.error('超时必须为正数')
    job = Job(ROOT, args.task_id, args.stage_timeout)
    try:
        if args.feedback_task:
            parent = read_json(ROOT / 'work' / 'tasks' / project_name(args.feedback_task) / 'status.json')
            if parent.get('state') != 'completed' or not parent.get('artifacts', {}).get('sheet'):
                raise ValueError('只允许为已完成且有决策单的任务录入复盘')
            if not args.metrics_data and not args.note and args.score is None:
                raise ValueError('请提供后台数据、评分或观察备注，不创建无依据复盘')
            cmd = [sys.executable, str(ROOT / 'scripts' / 'retrospect.py'), '--sheet', parent['artifacts']['sheet']]
            for value, flag in ((args.metrics_data, '--data'), (args.note, '--note'), (args.score, '--score'),
                                (args.match_column, '--match-column'), (args.match_value, '--match-value')):
                if value is not None:
                    cmd += [flag, str(value)]
            job.data['parent_task'] = args.feedback_task
            job.stage('后台数据匹配与复盘回灌', lambda: job.command(cmd, ROOT))
            job.finish('completed')
            return 0
        state = execute(args, forwarded, job)
        job.finish(state)
        return 4 if state == 'needs_review' else 0
    except BaseException as exc:
        state = 'cancelled' if isinstance(exc, (Cancelled, KeyboardInterrupt)) else 'failed'
        job.finish(state, f'{type(exc).__name__}: {exc}')
        return 130 if state == 'cancelled' else 1


if __name__ == '__main__':
    raise SystemExit(main())
