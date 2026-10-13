#!/usr/bin/env python3
r"""让剪映重新扫描草稿目录（新生成的草稿才会出现在列表里）。

背景：剪映只在**启动时**扫描草稿目录；运行中往目录里复制草稿不会自动出现
（联网学习结论 + 本机实测：回到首页只重写索引，不扫描新增文件夹）。

做法：用剪映自己的快捷键优雅退出（Ctrl+Q），等进程退干净，再重新启动并等待扫描。
安全：退出前会检查是否有草稿窗口处于编辑态，只有停在首页才自动重启。

用法: python jianying_refresh.py [--pid 21244] [--wait 45] [--dry-run]
"""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import argparse
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
EXE = Path(portable_path('user:AppData/Local/JianyingPro/Apps/JianyingPro.exe'))
DRAFT_ROOT = Path(portable_path('user:AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft'))


def processes() -> list[int]:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    pids = []
    for line in (r.stdout or "").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def index_names() -> list[str]:
    p = DRAFT_ROOT / "root_meta_info.json"
    if not p.exists():
        return []
    try:
        return [str(x.get("draft_name")) for x in json.loads(p.read_text(encoding="utf-8")).get("all_draft_store", [])]
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="重启剪映触发草稿扫描")
    ap.add_argument("--pid", type=int, help="当前剪映主进程；不填则自动找")
    ap.add_argument("--wait", type=int, default=45, help="启动后等待扫描的秒数")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pids = processes()
    if not pids:
        print("[状态] 剪映没在运行")
    else:
        import ui_agent as ua
        pid = args.pid or max(pids, key=lambda p: len([w for w in ua.windows(p)]))
        state = ua.page_state(pid)
        print(f"[状态] 剪映在运行 pid={pid} 当前={state}")
        if state == "编辑页":
            print("[停止] 有三个草稿正在编辑页里，自动重启可能打断你的操作。"
                  "请先按 Ctrl+Alt+Q 回到首页，或手工确认后再跑本脚本。")
            return 2
        if args.dry_run:
            print("[试运行] 未重启")
            return 0
        print("[退出] 发送 Ctrl+Q（剪映自身的退出快捷键）")
        ua.main_window("剪映专业版", pid)["control"].SetActive()
        time.sleep(0.6)
        ua.send("{Ctrl}q")
        for _ in range(20):
            time.sleep(1.0)
            if not processes():
                break
        if processes():
            print("[失败] 剪映没退出，停止")
            return 1
        print("[退出] 已完全退出")

    if args.dry_run:
        print("[试运行] 未启动")
        return 0
    print(f"[启动] {EXE}")
    subprocess.Popen([str(EXE)], close_fds=True)
    for i in range(args.wait):
        time.sleep(1.0)
        names = index_names()
        if i % 10 == 9:
            print(f"   等待扫描… {i+1}s，索引里已有 {len(names)} 个草稿")
    names = index_names()
    print(f"[完成] 剪映已启动，索引里 {len(names)} 个草稿")
    if names:
        print("   最新的几个：" + "、".join(names[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
