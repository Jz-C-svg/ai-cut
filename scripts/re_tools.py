"""AI CUT local interoperability analysis CLI; no upload or editor UI actions."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aicut import re_probe, native_draft


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    draft = commands.add_parser('draft')
    draft.add_argument('--project', required=True)
    draft.add_argument('--out', required=True)
    pe = commands.add_parser('pe')
    pe.add_argument('--binary', required=True)
    pe.add_argument('--out', required=True)
    read = commands.add_parser('readback')
    read.add_argument('--project', required=True)
    read.add_argument('--output-root')
    read.add_argument('--config', default=str(native_draft.CONFIG))
    commands.add_parser('doctor')
    args = parser.parse_args()
    if args.command == 'draft':
        report = re_probe.probe_project(args.project)
        path = re_probe.write_report(report, args.out)
        result = {'report': str(path), 'classifications': [x['kind'] for x in report['files']]}
    elif args.command == 'pe':
        report = re_probe.inspect_pe(args.binary)
        path = re_probe.write_report(report, args.out)
        result = {'report': str(path), 'exports': report['export_count'], 'tool': report['tool']}
    elif args.command == 'readback':
        report = native_draft.snapshot(args.project, args.output_root, config=args.config)
        result = {'snapshot_project': report['snapshot_project'], 'originals_unchanged': True,
                  'files': [{k: v for k, v in f.items() if k in ('name', 'method', 'snapshot_sha256')}
                            for f in report['files']], 'semantic_validation': 'pending'}
    else:
        codec = native_draft.pinned_codec()
        result = {'status': 'pinned_codec_available', 'dll_sha256': codec['dll_sha256'],
                  'mode': codec['mode'], 'tool_version': codec['pefile_version']}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
