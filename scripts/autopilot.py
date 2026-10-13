"""Scoped local make entry point for the AI CUT Director skill."""
import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aicut.autopilot import run
from aicut import cli


def main():
    cli.setup()
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--material')
    inputs.add_argument('--sheet', help='Reuse an existing plan; does not run a new model analysis')
    parser.add_argument('--goal', required=True)
    parser.add_argument('--mode', choices=['draft', 'mp4'], default='draft')
    parser.add_argument('--execute', action='store_true', help='Caller authorizes making a new local output for this brief')
    parser.add_argument('--copy-to-editor', action='store_true')
    parser.add_argument('--style', default='koubo-zhishi')
    parser.add_argument('--host-deliverable', choices=['draft', 'mp4'], default='draft',
                        help='Requested result inside Jianying; mp4 still requires host UI execution')
    parser.add_argument('--visible-operations', action='store_true',
                        help='Require observed UI placement/editing, not only opening a generated project')
    parser.add_argument('--summary', action='store_true', help='Print compact progress; full data stays in report_path')
    args = parser.parse_args()
    try:
        with redirect_stdout(sys.stderr):
            result = run(goal=args.goal, material=args.material, sheet=args.sheet, mode=args.mode,
                         execute=args.execute, copy_to_editor=args.copy_to_editor, style=args.style,
                         host_deliverable=args.host_deliverable, visible_operations=args.visible_operations)
    except (OSError, ValueError, TypeError) as exc:
        result = {'ok': False, 'status': 'blocked', 'error': str(exc)}
    displayed = ({k: result[k] for k in ('ok', 'status', 'task_id', 'brain_task_id', 'report_path',
                 'sheet', 'artifact', 'host_task', 'host_next_step', 'plan_origin', 'ready_to_ship',
                 'executed_in_editor', 'native_export_verified', 'error') if k in result}
                 if args.summary else result)
    print(json.dumps(displayed, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
