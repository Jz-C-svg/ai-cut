#!/usr/bin/env python3
r"""持续学习：按清单一条条学下去，学完就歇着等新主题。

每一轮调用 learn_batch.py 学 `--batch` 条（子进程隔离，单条失败不影响其他），
学完清单就进入长间隔轮询——你往 config/learn_topics.json 里加主题，它下一轮就会学。

用法:
  python learn_daemon.py --interval 4 --batch 2 --stop-file "work\learn.stop"
  python learn_daemon.py --list-only          # 看还剩多少没学

停止：出现停止文件（默认 work\learn.stop），或 Ctrl+C。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402
from aicut.paths import LOGS, WORK, python_exe  # noqa: E402

cli.setup()

SCRIPTS = ROOT / "scripts"
IDLE_MINUTES = 30  # 清单学完后多久看一次有没有新主题


def log(line: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    text = f"{stamp} | {line}"
    print(text, flush=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "learn-daemon.log").open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def pending_count() -> int:
    proc = subprocess.run([python_exe(), str(SCRIPTS / "learn_batch.py"), "--list"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    try:
        head = (proc.stdout or "").splitlines()[0]
        total = int(head.split("共 ")[1].split(" 条")[0])
        done = int(head.split("已学 ")[1].split(" 条")[0])
        return max(total - done, 0)
    except Exception:  # noqa: BLE001
        return -1


def main() -> int:
    ap = argparse.ArgumentParser(description="持续学习守护")
    ap.add_argument("--interval", type=float, default=4, help="两轮之间等多少分钟")
    ap.add_argument("--batch", type=int, default=2, help="每轮学几条")
    ap.add_argument("--minutes", type=float, default=0, help="总共跑多久（0=一直跑）")
    ap.add_argument("--stop-file", default=str(WORK / "learn.stop"), help="出现该文件就退出")
    ap.add_argument("--list-only", action="store_true", help="只看还剩多少")
    args = ap.parse_args()

    left = pending_count()
    if args.list_only:
        print(f"清单里还有 {left} 条没学")
        return 0

    stop = Path(args.stop_file)
    deadline = time.time() + args.minutes * 60 if args.minutes else None
    log(f"持续学习启动：每轮 {args.batch} 条，间隔 {args.interval} 分钟，剩余 {left} 条；停止文件 {stop}")

    idle = False
    while True:
        if stop.exists():
            log("检测到停止文件，退出")
            break
        if deadline and time.time() > deadline:
            log("到时间上限，退出")
            break

        proc = subprocess.run(
            [python_exe(), "-u", str(SCRIPTS / "learn_batch.py"), "--run", "--limit", str(args.batch)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}, timeout=1800,
        )
        out = (proc.stdout or "").strip()
        for line in out.splitlines():
            if line.strip():
                log("  " + line.strip()[:200])
        exhausted = "没有要学的了" in out
        if exhausted and not idle:
            log(f"清单学完，进入待机（每 {IDLE_MINUTES} 分钟看一次有没有新主题）")
            idle = True
        elif not exhausted:
            idle = False
        time.sleep((IDLE_MINUTES if idle else args.interval) * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
