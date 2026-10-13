#!/usr/bin/env python3
r"""指令执行器：把"人话指令"变成剪映里真实发生的操作，并留下可核对的日志。

按键全部来自本机实际快捷键方案（Config\Shortcut\combined.json，currentKeymapIndex=1 → Custom1）：
  分割=E  删除=D  撤销=Ctrl+Z  重做=Ctrl+Shift+Z  复制=Ctrl+C  剪切=Ctrl+X  粘贴=Ctrl+V
  全选=Ctrl+A  左裁=Q  右裁=W  批量分割=Ctrl+Shift+K  播放/暂停=Space
  上一个剪切点=Up  下一个剪切点=Down  前进=Shift+Right  后退=Shift+Left
  轨道加高=Ctrl+Alt+=  轨道变矮=Ctrl+Alt+-  时间线缩放=Ctrl+滚轮
  全屏=Ctrl+F  导出=Ctrl+M  返回草稿页=Ctrl+Alt+Q  退出=Ctrl+Q
注意：剪映没有"保存"快捷键，编辑靠自动保存；真正落盘要等自动保存或返回草稿页。

用法:
  python ui_do.py --pid 3968 --list
  python ui_do.py --pid 3968 分割
  python ui_do.py --pid 3968 key ctrl+shift+z
  python ui_do.py --pid 3968 wheel -5 --ctrl        # 时间线放大
  python ui_do.py --pid 3968 click 400 900          # 窗口内相对坐标点击
"""
from __future__ import annotations

import argparse
import ctypes
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ui_agent as ua  # noqa: E402
import ui_fast as F  # noqa: E402

LOG = ROOT / "09-logs" / "ui-actions.log"
user32 = ctypes.windll.user32

# 动作表：名称 -> (按键序列, 说明, 是否必须在编辑页)
ACTIONS: dict[str, tuple[str, str, bool]] = {
    "分割": ("{e}", "在播放头处分割选中片段", True),
    "删除": ("{Delete}", "删除选中片段（本机方案 D，这里用 Delete 等价安全）", True),
    "删除D": ("{d}", "按本机 Custom1 方案用 D 删除", True),
    "撤销": ("{Ctrl}z", "撤销上一步", True),
    "重做": ("{Ctrl}{Shift}z", "重做", True),
    "复制": ("{Ctrl}c", "复制选中片段", True),
    "剪切": ("{Ctrl}x", "剪切选中片段", True),
    "粘贴": ("{Ctrl}v", "粘贴", True),
    "全选": ("{Ctrl}a", "全选所有片段", True),
    "左裁": ("{q}", "裁掉选中片段播放头左侧", True),
    "右裁": ("{w}", "裁掉选中片段播放头右侧", True),
    "批量分割": ("{Ctrl}{Shift}k", "在播放头处批量分割所有轨道", True),
    "播放暂停": ("{Space}", "播放/暂停", True),
    "上一个剪切点": ("{Up}", "播放头跳到上一个剪切点", True),
    "下一个剪切点": ("{Down}", "播放头跳到下一个剪切点", True),
    "前进": ("{Shift}{Right}", "播放头前进（大步）", True),
    "后退": ("{Shift}{Left}", "播放头后退（大步）", True),
    "轨道加高": ("{Ctrl}{Alt}{=}", "轨道变高（时间线纵向放大）", True),
    "轨道变矮": ("{Ctrl}{Alt}{-}", "轨道变矮", True),
    "全屏预览": ("{Ctrl}f", "播放器全屏", True),
    "导出": ("{Ctrl}m", "打开导出面板（本机方案 Ctrl+M）", True),
    "返回草稿页": ("{Ctrl}{Alt}q", "回到草稿列表（会保存）", True),
    "退出剪映": ("{Ctrl}q", "退出剪映（危险，需确认）", True),
}


def log(pid: int, action: str, detail: str = "") -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | pid={pid} | {action} | {detail}\n")


def wheel(delta: int, ctrl: bool = False, pid: int | None = None) -> float:
    """滚轮：delta 正数向上（放大），负数向下；ctrl=True 时按住 Ctrl（时间线缩放）。"""
    return F.wheel(delta, ctrl)


def main() -> int:
    ap = argparse.ArgumentParser(description="剪映指令执行器")
    ap.add_argument("--app", default="剪映专业版")
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--list", action="store_true", help="列出可用动作")
    ap.add_argument("action", nargs="?", help="动作名，或用 key/wheel/click")
    ap.add_argument("args", nargs="*", help="key 的键序列 / wheel 的次数 / click 的 x y")
    ap.add_argument("--ctrl", action="store_true", help="wheel 时按住 Ctrl")
    ap.add_argument("--shot", action="store_true", help="执行前后各截一张图（审计）")
    ap.add_argument("--yes", action="store_true", help="确认执行危险动作（如退出剪映）")
    args = ap.parse_args()

    if args.list or not args.action:
        print(f"{'动作':12s} 说明")
        for k, (seq, desc, _ne) in ACTIONS.items():
            print(f"{k:12s} {desc}  [{seq}]")
        print("\n特殊：key <序列> / wheel <次数> [--ctrl] / click <x> <y>")
        return 0

    state = F.page_state(args.pid)
    win = F.main_window(args.pid)
    if state != "编辑页" and args.action not in ("返回草稿页", "退出剪映"):
        print(f"[拒执] 当前 {state}，不是编辑页；先打开草稿再操作")
        return 2
    if args.action == "退出剪映" and not args.yes:
        print("[拒执] 退出剪映属危险动作，请加 --yes")
        return 2

    F.activate(args.pid)
    if F.foreground_pid() != args.pid:
        print(f"[拒执] 拿不到剪映的前台焦点（当前前台 pid={F.foreground_pid()}），按键会打到别处")
        return 3
    before = None
    if args.shot:
        before = str(ua.shot(Path(ua.TMP) / f"do-before-{int(time.time())}.png"))

    act = args.action
    if act == "click" and len(args.args) >= 2:
        x, y = int(args.args[0]), int(args.args[1])
        F.activate(args.pid)
        if F.foreground_pid() != args.pid:
            print("[拒执] 点击前拿不到剪映焦点")
            return 3
        l, t, _w, _h = ua.rect_of(win)
        F.click(l + x, t + y, "instant", guard_pid=args.pid)
        detail = f"窗口内相对坐标 ({x},{y})"
    elif act == "wheel" and args.args:
        n = int(args.args[0])
        wheel(n, args.ctrl, args.pid)
        detail = f"滚轮 {n}{' +Ctrl' if args.ctrl else ''}"
    elif act == "key" and args.args:
        seq = args.args[0]
        F.send(seq)
        detail = f"原始按键 {seq}"
    elif act in ACTIONS:
        seq, desc, _ne = ACTIONS[act]
        F.send(seq)
        detail = f"{act}：{desc}"
    else:
        print(f"[未知动作] {act}（--list 看可用动作）")
        return 2

    time.sleep(0.8)
    after = None
    if args.shot:
        after = str(ua.shot(Path(ua.TMP) / f"do-after-{int(time.time())}.png"))
    log(args.pid, act, detail + (f" | 截图 {before} -> {after}" if args.shot else ""))
    print(f"[已执行] {detail}｜pid {args.pid}｜页面 {ua.page_state(args.pid)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
