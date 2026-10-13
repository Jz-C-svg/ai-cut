"""Inspect/resume a host task, record observed UI checkpoints, verify native MP4."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aicut import host_tasks
from aicut import cli
from aicut.production import read_json


def main():
    cli.setup()
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'resume', 'record', 'fail', 'verify-export'):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument('--task', required=True)
        command.add_argument('--summary', action='store_true')
        if name in ('record', 'fail'):
            command.add_argument('--step', required=True)
        if name == 'record':
            command.add_argument('--evidence', required=True, help='JSON with project/channel/observation/screenshots')
        if name == 'fail':
            command.add_argument('--reason', required=True)
    command = commands.add_parser('inspect-export', allow_abbrev=False)
    command.add_argument('--video', required=True)
    command.add_argument('--project-artifact', required=True)
    command.add_argument('--allow-silent', action='store_true')
    args = parser.parse_args()
    try:
        if args.command in ('status', 'resume'):
            result = host_tasks.status(args.task)
        elif args.command == 'record':
            result = host_tasks.record(args.task, args.step, read_json(args.evidence))
        elif args.command == 'fail':
            result = host_tasks.record(args.task, args.step, error=args.reason)
        elif args.command == 'verify-export':
            result = host_tasks.record(args.task, 'verify_export_media')
        else:
            expected = host_tasks.expected_media(args.project_artifact, not args.allow_silent)
            result = {'status': 'technical_validation_only', 'media': host_tasks.verify_media(args.video, expected),
                      'native_export_verified': False, 'ready_to_ship': False,
                      'note': 'Standalone inspection has no task-specific UI execution evidence'}
        if getattr(args, 'summary', False):
            result = {k: result[k] for k in ('schema', 'status', 'project', 'editor_project', 'host_task',
                      'next_step', 'executed_in_editor', 'native_export_verified', 'visible_placement_verified',
                      'ready_to_ship', 'visual_review', 'validation_error', 'note') if k in result}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'status': 'blocked', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
