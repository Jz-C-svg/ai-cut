#!/usr/bin/env python3
r"""剪辑主体：把自然语言目标变为步骤，素材制作先规划并等待确认。

感知（草稿 + 画面 + 停顿 + 识别结果 + 你的偏好）
  -> 规划（云端模型挑技能，断网退回规则）
  -> 执行（每步先快照）
  -> 验证（草稿 diff + 断言，不过就回滚重试）
  -> 记账（写进 memory.sqlite，可回查）

用法:
  # 先看它打算怎么剪（不落盘）
  python agent.py --draft 月子知识-成片 --goal "删掉开头 2 秒和结尾停顿" --dry-run

  # 真剪
  python agent.py --draft 月子知识-成片 --goal "剪掉所有停顿，压到 6 秒"

  # 从素材生成剪辑方案和待确认制作请求（完整规划流水线）
  python agent.py --material "work\normalized\月子知识-0299.mp4" --goal "按口播知识型出一条片子，封面写'选月子中心先问这一句'"

  python agent.py --draft X --goal "..." --json --out work/agent-report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402
from aicut.agent import Agent  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 剪辑主体")
    ap.add_argument("--goal", required=True, help="一句话目标")
    ap.add_argument("--draft", help="要剪的草稿（名字或路径）")
    ap.add_argument("--material", help="素材路径（没有草稿时用，配合出片类目标）")
    ap.add_argument("--video", help="源素材（用于停顿/画面分析，默认取草稿里的素材）")
    ap.add_argument("--dry-run", action="store_true", help="只演算，不落盘")
    ap.add_argument("--rules", action="store_true", help="跳过模型，用规则规划")
    ap.add_argument("--provider", default="auto", choices=["auto", "deepseek", "ollama"])
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--retries", type=int, default=1, help="单步失败后的重试次数")
    ap.add_argument("--no-vision", action="store_true", help="不做画面分析（更快）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="把任务报告写到文件")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument('--sheet', help='已有大脑决策单：通过制作接口检查并等待确认')
    ap.add_argument('--software', default='jianying')
    ap.add_argument('--engine', choices=['project', 'portable', 'native'], default='project')
    ap.add_argument('--output', help='独立MP4输出路径')
    ap.add_argument('--copy-to-editor', action='store_true', help='检查方案时明确选择复制到剪映草稿库')
    ap.add_argument('--production-request', help='已检查的制作请求ID')
    ap.add_argument('--approve-request-sha256', help='用户确认的制作请求哈希，不能由模型生成')
    args = ap.parse_args()

    draft = cli.resolve_draft(args.draft) if args.draft else None
    if not draft and not args.material and not args.sheet and not args.production_request:
        raise SystemExit('至少要给 --draft、--material、--sheet 或 --production-request')
    if bool(args.production_request) != bool(args.approve_request_sha256):
        raise SystemExit('执行制作请求需要同时提供请求ID和用户确认的请求哈希')
    if args.sheet and args.production_request:
        raise SystemExit('--sheet 和 --production-request 不能同时使用')
    production = {'software': args.software, 'engine': args.engine,
                  'output': args.output, 'publish': args.copy_to_editor}
    if args.sheet:
        production['sheet'] = str(Path(args.sheet).resolve())
    if args.production_request:
        production = {'request_id': args.production_request,
                      'approvals': {args.production_request: args.approve_request_sha256}}

    logs: list[str] = []

    def log(line: str) -> None:
        logs.append(str(line))
        if not args.quiet:
            print(line)

    agent = Agent(draft=draft, video=args.video or args.material, goal=args.goal,
                  dry_run=args.dry_run, provider=args.provider, max_steps=args.max_steps,
                  retries=args.retries, use_rules=args.rules, use_vision=not args.no_vision,
                  log=log, production=production)
    result = agent.run()
    result["log"] = logs

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.quiet:
            print(f"任务报告：{args.out}")

    if args.json:
        cli.print_json(result)
        return 0 if result["ok"] else 1

    print("")
    status = result.get('status') or ('完成' if result['ok'] else '未完成')
    print(f"任务 {result['task']}｜{status}｜"
          f"{result['duration']['start']:.2f}s -> {result['duration']['end']:.2f}s｜"
          f"耗时 {result['seconds']}s")
    if result.get("created"):
        print("新草稿：" + "、".join(result["created"]))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
