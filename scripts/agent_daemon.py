#!/usr/bin/env python3
r"""常驻主体：盯着一个目录，来新素材就自己开工。

安全默认：
  · 只读素材，绝不删原件
  · 只在 06-drafts 里生成新草稿，**不擅自发布进剪映草稿目录**（要发布自己加 --publish）
  · 每个文件一份任务报告，写进 work/agent-reports/
  · 已处理过的文件记在记忆库里，重启不会重复剪

用法:
  python agent_daemon.py --watch "<project-root>\00-inbox" --once      # 跑一轮就退出（适合手动/计划任务）
  python agent_daemon.py --watch "<project-root>\00-inbox" --interval 30
  python agent_daemon.py --watch ... --goal "剪掉所有停顿并压到 30 秒"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, plan as plan_mod  # noqa: E402
from aicut.agent import Agent  # noqa: E402
from aicut.memory import open_memory  # noqa: E402
from aicut.paths import LOGS, WORK  # noqa: E402

cli.setup()

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".MP4", ".MOV"}


def new_videos(watch: Path) -> list[Path]:
    return sorted(p for p in watch.rglob("*") if p.suffix in VIDEO_EXT and p.is_file())


def main() -> int:
    ap = argparse.ArgumentParser(description="盯目录自动剪辑")
    ap.add_argument("--watch", required=True, help="素材投放目录（如 00-inbox）")
    ap.add_argument("--goal", default="把这条素材按口播知识型出片，删掉停顿和口误",
                    help="给主体的一句话目标")
    ap.add_argument("--style", default="koubo-zhishi", help="规则模式下使用的风格卡")
    ap.add_argument("--interval", type=int, default=30, help="轮询间隔秒数")
    ap.add_argument("--once", action="store_true", help="跑一轮就退出")
    ap.add_argument("--publish", action="store_true", help="生成后发布到剪映草稿目录")
    ap.add_argument("--limit", type=int, default=3, help="单轮最多处理几个素材")
    ap.add_argument("--rules", action="store_true", help="不用模型，纯规则规划")
    args = ap.parse_args()

    watch = Path(args.watch)
    watch.mkdir(parents=True, exist_ok=True)
    mem = open_memory()
    reports = WORK / "agent-reports"
    reports.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / "agent-daemon.log"

    def log(line: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        text = f"{stamp} | {line}"
        print(text, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    log(f"守护启动：盯 {watch}，目标「{args.goal}」，间隔 {args.interval}s"
        + ("，单轮模式" if args.once else ""))
    while True:
        done = mem.fact("daemon:done", []) or []
        pending = [p for p in new_videos(watch) if str(p) not in done][: args.limit]
        if pending:
            log(f"发现 {len(pending)} 个待处理素材")
        for video in pending:
            goal = args.goal
            goal = goal + f"（风格用 {args.style}）"
            log(f"开工：{video.name}")
            agent = Agent(video=video, goal=goal, use_rules=args.rules, log=log)
            result = agent.run()
            result["material"] = str(video)
            if args.publish and result.get("created"):
                for name in result["created"]:
                    log(f"提示：如需在剪映里看到 {name}，请跑 jianying_refresh.py 重启剪映")
            out = reports / f"{time.strftime('%Y%m%d-%H%M%S')}-{video.stem}.json"
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            done = mem.fact("daemon:done", []) or []
            done.append(str(video))
            mem.set_fact("daemon:done", done, source="daemon")
            log(f"完成：{video.name} → {'、'.join(result['created']) or '无新草稿'}｜报告 {out.name}")
        if args.once:
            break
        time.sleep(max(args.interval, 5))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
