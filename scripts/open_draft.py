#!/usr/bin/env python3
r"""在剪映首页打开草稿（默认打开第一张卡片＝最新生成的草稿）。

判据是确定性的：剪映首页窗口类为 HomePage_QMLTYPE，进入编辑页后变成 MainWindow_QMLTYPE，
所以"有没有打开成功"直接看窗口类，不用问模型。

用法: python open_draft.py [--pid 21244] [--index 1] [--candidates "280,585;280,545"]
"""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import argparse
import io
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts"))
import ui_agent as ua  # noqa: E402
import ui_fast as F  # noqa: E402

DEFAULT_CANDIDATES = [(280, 585), (280, 545), (240, 585), (320, 585), (250, 505)]


def main() -> int:
    ap = argparse.ArgumentParser(description="在剪映首页打开草稿")
    ap.add_argument("--app", default="剪映专业版")
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--candidates", help="分号分隔的相对坐标，如 280,585;280,545")
    args = ap.parse_args()

    state = ua.page_state(args.pid)
    print(f"[状态] {state}")
    if state == "编辑页":
        print("[跳过] 已经在编辑页了")
        return 0
    if state != "首页":
        print("[失败] 没找到剪映首页窗口（剪映可能没启动）")
        return 1

    win = F.main_window(args.pid)
    if win is None:
        print("[失败] 找不到剪映窗口")
        return 1
    l, t, r, b = win["rect"]
    w, h = r - l, b - t
    home = {"control": None}
    print(f"[首页窗口] {w}x{h} 原点 ({l},{t})")
    F.activate(args.pid)
    time.sleep(0.4)

    if args.candidates:
        cands = [tuple(int(v) for v in c.split(",")) for c in args.candidates.split(";") if c.strip()]
    else:
        cands = list(DEFAULT_CANDIDATES)
        el = ua.mem_get(args.app, "首页第一张草稿卡片")
        if el:   # 直接用记忆里的相对偏移，避免和别的窗口坐标系混用
            cands.insert(0, (int(el["偏移"][0]), int(el["偏移"][1])))
            print(f"[记忆] 首页第一张草稿卡片 相对坐标 {cands[0]}（命中 {el.get('命中',0)}）")

    for i, (x, y) in enumerate(cands, 1):
        print(f"[尝试 {i}] 双击相对坐标 ({x},{y})")
        sx, sy = l + x, t + y
        for _ in range(3):                      # 抢不到焦点就重试，别直接崩
            F.activate(args.pid)
            if F.foreground_pid() == args.pid:
                break
            time.sleep(0.3)
        if F.foreground_pid() != args.pid:
            print("      拿不到剪映焦点，跳过这次点击")
            continue
        try:
            F.click(sx, sy, "instant", guard_pid=args.pid)
        except Exception as exc:
            print(f"      点击被拦：{exc}")
            continue
        time.sleep(0.08)
        try:
            F.click(sx, sy, "instant", guard_pid=args.pid)
        except Exception:
            pass
        time.sleep(3.0)
        if F.page_state(args.pid) == "编辑页":
            print("[成功] 已进入编辑页")
            p = ua.shot(Path(portable_path('user:AppData/Local/Temp/opened.png')))
            print(f"[截图] {p}")
            ua.mem_put(args.app, "首页第一张草稿卡片", (x, y), (w, h), "左上",
                       "双击后窗口类 HomePage -> MainWindow，成功打开草稿")
            return 0
    print("[失败] 候选坐标都没能打开草稿")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
