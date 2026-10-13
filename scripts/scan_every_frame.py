"""Full local frame-by-frame visual inference, resumable by media/model identity."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aicut.framewise import scan

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', required=True)
    parser.add_argument('--limit', type=int, help='Diagnostic only: report incomplete unless all frames are covered')
    parser.add_argument('--normalized-input', action='store_true', help='Diagnostic: scan the supplied file instead of tracing its original')
    parser.add_argument('--status-out', help='Copy the final status JSON to this path for pipeline handoff')
    args = parser.parse_args()
    result = scan(args.video, limit=args.limit, original=not args.normalized_input)
    if args.status_out:
        from aicut.production import atomic_json
        atomic_json(Path(args.status_out), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['complete'] else 4)
