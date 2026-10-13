"""One delivery entry point. Never equate portable rendering with editor execution."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'scripts')]
from aicut.editor_capabilities import capabilities, preflight
from aicut.production import project_name, atomic_json


def inspect_plan(sheet_path, software='jianying', engine='project'):
    """Read exactly the bytes a user will approve, and explain execution blockers."""
    import build_draft
    path = Path(sheet_path).resolve()
    raw = path.read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('决策单超过 2MB')
    sheet = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(sheet, dict):
        raise ValueError('决策单必须为 JSON 对象')
    problems = build_draft.quality_gates(sheet)
    warnings = [p for p in problems if p.startswith(('缺少 cover_text', '缺少 cta'))]
    blockers = [p for p in problems if p not in warnings]
    try:
        preflight(sheet, software, engine)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        blockers.append(str(exc))
    clips = sheet.get('clips') if isinstance(sheet.get('clips'), list) else []
    total = 0.0
    for clip in clips:
        try:
            total += (float(clip['end']) - float(clip['start'])) / float(clip.get('speed', 1))
        except (TypeError, ValueError, KeyError, ZeroDivisionError):
            total = None
            break
    if total is not None and not math.isfinite(total):
        total = None
    return {'sheet': str(path), 'sheet_sha256': hashlib.sha256(raw).hexdigest(),
            'plan': sheet, 'software': software, 'engine': engine,
            'ready': not blockers, 'blockers': list(dict.fromkeys(blockers)), 'warnings': warnings,
            'summary': {'project': sheet.get('project'), 'clip_count': len(clips),
                        'duration': total, 'width': sheet.get('width', 1080),
                        'height': sheet.get('height', 1920),
                        'keyframe_count': sum(len(c.get('keyframes') or []) for c in clips if isinstance(c, dict))}}


def deliver(sheet_path, software='jianying', engine='project', output=None,
            approved_sha256=None, publish=False, report_out=None):
    import build_draft
    path = Path(sheet_path).resolve()
    run_dir = ROOT / 'work' / 'deliveries' / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    report_path = Path(report_out).resolve() if report_out else run_dir / 'delivery.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'aicut-delivery-1', 'software': software, 'engine': engine,
              'sheet': str(path), 'status': 'preflight',
              'executed_in_editor': False, 'host_render_verified': False,
              'visual_review': 'pending', 'artifacts': {}}
    def save():
        atomic_json(report_path, report)
    save()
    try:
        # Hash and execute the same snapshot, even if another process edits the file.
        sheet_bytes = path.read_bytes()
        sheet = json.loads(sheet_bytes.decode('utf-8-sig'))
        name = project_name(sheet['project'])
        digest = hashlib.sha256(sheet_bytes).hexdigest()
        snapshot = run_dir / 'sheet.snapshot.json'
        snapshot.write_bytes(sheet_bytes)
        report['sheet_sha256'] = digest
        report['artifacts']['plan_snapshot'] = str(snapshot)
        if (sheet.get('requires_plan_confirmation') or approved_sha256 is not None) and approved_sha256 != digest:
            raise ValueError('Current plan version has not been approved')
        preflight(sheet, software, engine)
        problems = build_draft.quality_gates(sheet)
        hard = [x for x in problems if not x.startswith(('缺少 cover_text', '缺少 cta'))]
        if hard:
            raise ValueError('; '.join(hard))
        report['warnings'] = problems
        if publish and (software != 'jianying' or engine != 'project'):
            raise ValueError('Publish is supported only for a Jianying project delivery')
        if engine == 'project':
            if software == 'jianying':
                draft = build_draft.build(sheet, build_draft.load_config())
                if publish:
                    build_draft.publish(draft, build_draft.load_config())
                artifact = draft / 'draft_content.json'
            else:
                artifact = build_draft.build_for_software(sheet, software)
            report['status'] = 'project_generated_pending_host_verification'
            report['artifacts']['project'] = str(artifact)
        else:
            import export_video
            target = Path(output).resolve() if output else ROOT / '07-output' / f'{name}.{software}.portable.mp4'
            if target.suffix.lower() != '.mp4':
                raise ValueError('Portable output must be MP4')
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(f'{target.stem}.{uuid.uuid4().hex}.partial.mp4')
            report['status'] = 'rendering'; save()
            cmd = export_video.build_command(sheet, staged, True)
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
            (run_dir / 'render.log').write_text(result.stderr or '', encoding='utf-8')
            if result.returncode:
                raise RuntimeError(f'FFmpeg failed; see {run_dir / "render.log"}')
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(staged)],
                                   capture_output=True, text=True, check=True, timeout=30)
            media = json.loads(probe.stdout)
            duration = float(media['format']['duration'])
            expected = sum((float(c['end']) - float(c['start'])) / float(c.get('speed', 1)) for c in sheet['clips'])
            video = next(s for s in media['streams'] if s['codec_type'] == 'video')
            if abs(duration - expected) > .2 or video['width'] != int(sheet.get('width', 1080)) or video['height'] != int(sheet.get('height', 1920)):
                raise RuntimeError('Rendered duration or dimensions differ from plan')
            if not any(s['codec_type'] == 'audio' for s in media['streams']):
                raise RuntimeError('Rendered audio stream missing')
            decode = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(staged), '-f', 'null', '-'],
                                    capture_output=True, text=True, encoding='utf-8', errors='replace')
            if decode.returncode:
                raise RuntimeError('Full decode verification failed: ' + decode.stderr[-500:])
            report['media_validation'] = {'duration': duration, 'expected': expected,
                                          'width': video['width'], 'height': video['height'],
                                          'full_decode_passed': True, 'audio_present': True}
            os.replace(staged, target)
            with target.open('rb') as stream:
                report['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
            report['artifacts']['video'] = str(target)
            from review_render import review_video
            report['artifacts']['frame_review'] = str(review_video(target, snapshot))
            report['status'] = 'rendered_pending_visual_review'
    except (ValueError, KeyError, TypeError) as exc:
        report['status'] = 'blocked'; report['error'] = str(exc)
    except Exception as exc:
        report['status'] = 'failed'; report['error'] = f'{type(exc).__name__}: {exc}'
    save()
    print('DELIVERY:' + str(report_path), flush=True)
    return report_path, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sheet')
    parser.add_argument('--software', default='jianying')
    parser.add_argument('--engine', choices=['project', 'portable', 'native'], default='project')
    parser.add_argument('--out')
    parser.add_argument('--approve-plan-sha256')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--report-out')
    parser.add_argument('--capabilities', action='store_true')
    args = parser.parse_args()
    if args.capabilities:
        print(json.dumps(capabilities(), ensure_ascii=False, indent=2)); return 0
    if not args.sheet:
        parser.error('--sheet is required')
    _, result = deliver(args.sheet, args.software, args.engine, args.out, args.approve_plan_sha256, args.publish, args.report_out)
    return 0 if result['status'] in {'project_generated_pending_host_verification', 'rendered_pending_visual_review'} else 4


if __name__ == '__main__':
    raise SystemExit(main())
