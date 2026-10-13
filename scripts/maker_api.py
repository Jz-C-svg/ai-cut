"""Local callable video production interface; uses shared project/FFmpeg writers."""
import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aicut.maker_api import MakerAPI, tool_definitions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('tools', help='Print the model function schemas')
    inspect = commands.add_parser('inspect', help='Check a brain decision sheet and prepare a request')
    inspect.add_argument('--sheet', required=True)
    inspect.add_argument('--software', default='jianying')
    inspect.add_argument('--engine', choices=['project', 'portable', 'native'], default='project')
    inspect.add_argument('--out')
    inspect.add_argument('--copy-to-editor', action='store_true')
    execute = commands.add_parser('execute', help='Execute the exact user-approved request')
    execute.add_argument('--request', required=True)
    execute.add_argument('--approve-request-sha256', required=True)
    args = parser.parse_args()
    try:
        api = MakerAPI()
        if args.command == 'tools':
            result = {'tools': tool_definitions()}
        elif args.command == 'inspect':
            with redirect_stdout(sys.stderr):
                result = api.prepare(args.sheet, args.software, args.engine, args.out, args.copy_to_editor)
        else:
            with redirect_stdout(sys.stderr):
                result = api.execute(args.request, approved_request_sha256=args.approve_request_sha256)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'ok': False, 'status': 'blocked', 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ok', True) else 2


if __name__ == '__main__':
    raise SystemExit(main())
