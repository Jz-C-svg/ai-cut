"""Inspect the complete searchable learning corpus without calling a model."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aicut.learning_context import catalog, retrieve


def main():
    parser = argparse.ArgumentParser(description='学习库覆盖清单与检索预览（不调用模型）')
    parser.add_argument('--query', help='当前视频主题、制作目标或待解决问题')
    parser.add_argument('--out', help='保存 JSON 检索证据')
    parser.add_argument('--platform', choices=['douyin', 'bilibili', 'all'], help='平台知识筛选；未指定时从问题识别')
    args = parser.parse_args()
    if args.query:
        result = retrieve(args.query, platform=args.platform)
    else:
        data = catalog()
        result = {k: data[k] for k in ('coverage', 'file_count', 'signature', 'warnings')}
        result['indexed_chunks'] = len(data['docs'])
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
