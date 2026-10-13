#!/usr/bin/env python3
r"""界面代理：快速鼠标 + 元素记忆 + 视觉兜底（给剪辑软件做自动化用）。

设计要点（解决"每次都要视觉现猜、还猜不准"的问题）：
  1. 快速鼠标：直接用 SetCursorPos + mouse_event，瞬时到位；需要过程动画时用 3 档速率。
  2. 元素记忆：定位一次就把坐标按"窗口锚点"存进 config/ui_elements.json，下次直接复用，不再问模型。
  3. 视觉兜底：找不到记忆时才截图问模型，而且问的是"局部裁剪 + 只要一行文字"，
     比整屏开放式提问准得多（整屏提问模型容易开始长篇推理）。

用法示例：
  python ui_agent.py speedtest                     # 测鼠标移动速率
  python ui_agent.py mem --app 剪映专业版           # 看已记住的元素
  python ui_agent.py learn --app 剪映专业版 --name 菜单按钮 --x 135 --y 22 --evidence "点开后出现 MenuWindow"
  python ui_agent.py click --app 剪映专业版 --pid 24084 --name 菜单按钮
  python ui_agent.py scan-menu --app 剪映专业版 --pid 24084 --want 返回首页
"""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import argparse
import base64
import ctypes
import io
import json
import subprocess
from ctypes import wintypes
import sys
import time
from pathlib import Path

if str(getattr(sys.stdout, "encoding", "")).lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
MEM_FILE = ROOT / "config" / "ui_elements.json"
TMP = Path(portable_path('user:AppData/Local/Temp'))

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

import ui_bridge  # noqa: E402
import understand  # noqa: E402
import uiautomation as auto  # noqa: E402


# ---------------- 记忆 ----------------

def mem_load() -> dict:
    if MEM_FILE.exists():
        return json.loads(MEM_FILE.read_text(encoding="utf-8"))
    return {"说明": "界面元素记忆：定位成功一次就存下来，下次直接复用。偏移量相对窗口锚点。", "应用": {}}


def mem_save(data: dict) -> None:
    MEM_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEM_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def mem_put(app: str, name: str, offset: tuple[int, int], size: tuple[int, int],
            anchor: str, evidence: str) -> None:
    data = mem_load()
    apps = data.setdefault("应用", {})
    a = apps.setdefault(app, {"元素": {}})
    a["基准尺寸"] = list(size)
    el = a["元素"].setdefault(name, {})
    el.update({"偏移": [int(offset[0]), int(offset[1])], "锚点": anchor,
               "证据": evidence, "命中": int(el.get("命中", 0)),
               "更新时间": time.strftime("%Y-%m-%d %H:%M:%S")})
    mem_save(data)


def mem_get(app: str, name: str) -> dict | None:
    return ((mem_load().get("应用", {}).get(app) or {}).get("元素") or {}).get(name)


# ---------------- 窗口 ----------------

def windows(pid: int) -> list[dict]:
    return [w for w in ui_bridge.enum_windows() if w["pid"] == pid]


PAGE_CLASSES = ("MainWindow", "HomePage")   # 剪映：编辑页 MainWindow_QMLTYPE / 首页 HomePage_QMLTYPE
EXCLUDE_CLASSES = ("MenuWindow", "ToolTip")   # 菜单与提示是独立顶层窗口，不能当成主窗口


def main_window(app: str, pid: int | None) -> dict:
    """只认主窗口：避开剪映的 MenuWindow / ToolTip 这类独立顶层窗口。"""
    pool = windows(pid) if pid else ui_bridge.enum_windows()
    best = None
    for w in pool:
        cls = w.get("class") or ""
        if any(x in cls for x in EXCLUDE_CLASSES):
            continue
        if any(x in cls for x in PAGE_CLASSES) and w["width"] > 600:
            if best is None or w["area"] > best["area"]:
                best = w
    if best is not None:
        return best
    return ui_bridge.find_target(app, pid=pid)


def find_pids(proc: str = "JianyingPro.exe") -> list[int]:
    """查某个进程名下的所有 pid（tasklist 输出是 GBK 编码）。"""
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {proc}", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    pids = []
    for line in (r.stdout or "").splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) >= 2 and cols[1].isdigit():
            pids.append(int(cols[1]))
    return sorted(set(pids))


def page_state(pid: int) -> str:
    """用窗口类判断当前页面：编辑页 / 首页。

    统一走 ui_fast（只认可见窗口 + hwnd→UIA 类名），避免两套实现自相矛盾。
    """
    try:
        import ui_fast as _F
        return _F.page_state(pid)
    except Exception:
        pass
    cls = [w["class"] for w in windows(pid)]
    if any("MainWindow" in c for c in cls):
        return "编辑页"
    if any("HomePage" in c for c in cls):
        return "首页"
    return "未知"


def rect_of(target: dict) -> tuple[int, int, int, int]:
    r = target["control"].BoundingRectangle
    return int(r.left), int(r.top), int(r.width()), int(r.height())


# ---------------- 快速鼠标 / 键盘 ----------------

MOVE_SPEED = {"instant": 0, "fast": 6, "normal": 20}


kernel32 = ctypes.windll.kernel32
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001


def force_foreground(hwnd: int) -> None:
    """Windows 前台锁的常规绕法：附加线程输入 + 置顶再取消置顶。"""
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True))
    try:
        user32.ShowWindow(hwnd, 9)                                   # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(tid_me, tid_fg, False)


def activate(app: str, pid: int, timeout: float = 6.0) -> bool:
    """把目标窗口弄到前台并确认焦点真的在它身上（否则按键会打给别人）。"""
    win = main_window(app, pid)
    hwnd = int(win["control"].NativeWindowHandle)
    end = time.time() + timeout
    while time.time() < end:
        if foreground_pid() == pid:
            return True
        force_foreground(hwnd)
        time.sleep(0.3)
        if foreground_pid() == pid:
            return True
        try:
            win["control"].SetActive()
        except Exception:
            pass
        time.sleep(0.2)
    return foreground_pid() == pid


def move(x: int, y: int, speed: str = "instant") -> float:
    """移动鼠标。instant 直接 SetCursorPos（最快），fast/normal 走过程动画。"""
    t0 = time.perf_counter()
    steps = MOVE_SPEED.get(speed, 0)
    if steps <= 0:
        user32.SetCursorPos(int(x), int(y))
    else:
        cur = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(cur))
        for i in range(1, steps + 1):
            user32.SetCursorPos(int(cur.x + (x - cur.x) * i / steps),
                                int(cur.y + (y - cur.y) * i / steps))
            time.sleep(0.004)
    return (time.perf_counter() - t0) * 1000


def foreground_pid() -> int:
    hwnd = user32.GetForegroundWindow()
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def click(x: int, y: int, speed: str = "instant", button: str = "left",
          guard_pid: int | None = None) -> float:
    """guard_pid 不为空时，只有该进程的窗口在前台才点击（防止误点别人的窗口）。"""
    if guard_pid is not None and foreground_pid() != guard_pid:
        raise RuntimeError(f"前台窗口不属于 pid {guard_pid}，已拒绝点击")
    ms = move(x, y, speed)
    down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
    user32.mouse_event(down, 0, 0, 0, 0)
    user32.mouse_event(up, 0, 0, 0, 0)
    return ms


def send(seq: str, interval: float = 0.05) -> None:
    auto.SendKeys(seq, waitTime=interval)


# ---------------- 截图与视觉兜底 ----------------

def shot(path: Path, box: tuple[int, int, int, int] | None = None) -> Path:
    from PIL import ImageGrab
    if box is None:
        r = auto.GetRootControl().BoundingRectangle
        box = (int(r.left), int(r.top), int(r.right), int(r.bottom))
    path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=box, all_screens=True).save(path)
    return path


def crop(src: Path, box: tuple[int, int, int, int], scale: int, out: Path) -> Path:
    from PIL import Image
    im = Image.open(src).crop(box)
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    im.save(out)
    return out


def ask_text(img: Path, prompt: str, max_chars: int = 60) -> str:
    """问模型一句短问题，只要一行答案；过长（模型开始推理）就判为失败。"""
    b = base64.b64encode(img.read_bytes()).decode()
    txt = understand.vision_cloud(b, understand.load_key(), prompt).strip()
    if not txt or len(txt) > max_chars * 4:
        return ""
    for line in txt.splitlines():
        line = line.strip(" 　*-‣")
        if line and len(line) <= max_chars:
            return line
    return ""


def read_row(image: Path, box: tuple[int, int, int, int]) -> str:
    """裁出界面上一行 → 放大 → 问模型这一行写的是什么（比整屏提问准很多）。"""
    from PIL import Image
    im = Image.open(image).crop(box)
    im = im.resize((im.width * 3, im.height * 3), Image.LANCZOS)
    p = TMP / "ui-agent-row.png"
    im.save(p)
    return ask_text(p, "这张图是软件界面上一行的放大截图。只回答这一行里的文字，不要解释，"
                       "看不清就回答 看不清。", max_chars=24)


# ---------------- 记忆 + 视觉兜底动作 ----------------

def locate(app: str, name: str, pid: int | None = None) -> tuple[int, int, dict]:
    t = main_window(app, pid)
    l, tp, w, h = rect_of(t)
    el = mem_get(app, name)
    if el:
        dx, dy = el["偏移"]
        anchor = el.get("锚点", "左上")
        x = l + dx if anchor in ("左上", "左下") else l + w + dx
        y = tp + dy if anchor in ("左上", "右上") else tp + h + dy
        return x, y, {"来源": "记忆", "元素": el}
    raise KeyError(f"没有 {app} 的元素记忆: {name}（用 learn 记一次，或先 scan-menu 自动学习）")


def do_click(app: str, pid: int | None, name: str, speed: str) -> int:
    x, y, info = locate(app, name, pid)
    ms = click(x, y, speed, guard_pid=pid if pid else None)
    el = info.get("元素") or {}
    if el:
        data = mem_load()
        el2 = data["应用"][app]["元素"][name]
        el2["命中"] = int(el2.get("命中", 0)) + 1
        data["应用"][app]["元素"][name] = el2
        mem_save(data)
    print(f"[点击] {app}.{name} -> ({x},{y}) 用时 {ms:.1f}ms（{info['来源']}，累计命中 "
          f"{(mem_get(app, name) or {}).get('命中')}）")
    return 0


def scan_menu(app: str, pid: int, want: str, y0: int, y1: int, step: int,
              menu_btn: str, dry_run: bool) -> int:
    """打开菜单后逐行裁剪识别，命中目标项才点击。"""
    t = main_window(app, pid)
    t["control"].SetActive()
    time.sleep(0.6)

    before = {(w["class"], w["width"], w["height"]) for w in windows(pid)}
    do_click(app, pid, menu_btn, "instant")
    time.sleep(0.9)
    opened = any("MenuWindow" in (w.get("class") or "") for w in windows(pid))
    if not opened:
        print("[菜单] 没展开，再点一次")
        do_click(app, pid, menu_btn, "instant")
        time.sleep(0.9)
        opened = any("MenuWindow" in (w.get("class") or "") for w in windows(pid))
    if not opened:
        print("[失败] 菜单没打开")
        return 1
    print("[菜单] 已展开")

    l, tp, w, h = rect_of(t)
    full = shot(TMP / "ui-agent-menu.png")
    for y in range(y0, y1 + 1, step):
        move(l + 170, tp + y, "instant")
        time.sleep(0.25)
        full = shot(TMP / "ui-agent-menu.png")
        row = read_row(full, (60, y - int(step * 0.7), 320, y + int(step * 0.7)))
        print(f"[y={y}] {row}")
        if want and want in row:
            if dry_run:
                print("[试运行] 未点击")
                return 0
            ms = click(l + 170, tp + y, "instant")
            print(f"[点击] {want} <- y={y} 用时 {ms:.1f}ms")
            time.sleep(2.0)
            after = {(w["class"], w["width"], w["height"]) for w in windows(pid)}
            print("[窗口变化]", "有" if after != before else "无")
            return 0
    print("[失败] 扫描范围内没找到目标项（未点击）")
    send("{Escape}")
    return 1


def speedtest(times: int = 30) -> int:
    """只测调用耗时，不移动鼠标：目标点始终等于当前点。"""
    cur = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(cur))
    print(f"[安全测速] 鼠标保持在 ({cur.x},{cur.y}) 不动，只统计调用耗时")
    for label in ("instant", "fast", "normal"):
        t0 = time.perf_counter()
        for _ in range(times):
            move(cur.x, cur.y, label)
        dt = (time.perf_counter() - t0) / times * 1000
        print(f"[速率] {label:8s} 平均每次 {dt:.2f} ms")
    t0 = time.perf_counter()
    for _ in range(times):
        auto.MoveTo(cur.x, cur.y)
    dt = (time.perf_counter() - t0) / times * 1000
    print(f"[对照] uiautomation.MoveTo 平均每次 {dt:.2f} ms")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="界面代理")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pids")
    p.add_argument("--proc", default="JianyingPro.exe")

    p = sub.add_parser("speedtest")
    p.add_argument("--times", type=int, default=20)

    p = sub.add_parser("mem")
    p.add_argument("--app")

    p = sub.add_parser("learn")
    p.add_argument("--app", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--x", type=int, required=True)
    p.add_argument("--y", type=int, required=True)
    p.add_argument("--anchor", default="左上", choices=["左上", "左下", "右上", "右下"])
    p.add_argument("--evidence", default="")
    p.add_argument("--pid", type=int)

    p = sub.add_parser("where")
    p.add_argument("--app", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--pid", type=int)

    for name in ("click", "move", "hover"):
        p = sub.add_parser(name)
        p.add_argument("--app", required=True)
        p.add_argument("--name", required=True)
        p.add_argument("--pid", type=int)
        p.add_argument("--speed", default="instant", choices=list(MOVE_SPEED))

    p = sub.add_parser("shot")
    p.add_argument("--out", default=str(TMP / "ui-agent-shot.png"))

    p = sub.add_parser("scan-menu")
    p.add_argument("--app", required=True)
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--want", default="返回首页")
    p.add_argument("--y0", type=int, default=330)
    p.add_argument("--y1", type=int, default=560)
    p.add_argument("--step", type=int, default=16)
    p.add_argument("--menu-btn", default="菜单按钮")
    p.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()

    if args.cmd == "pids":
        for pid in find_pids(args.proc):
            wins = [w for w in windows(pid) if w["width"] > 200]
            print(f"pid {pid} | {page_state(pid)} | "
                  f"{[(w['class'][:22], w['width'], w['height']) for w in wins]}")
        return 0
    if args.cmd == "speedtest":
        return speedtest(args.times)
    if args.cmd == "mem":
        data = mem_load()
        apps = data.get("应用", {})
        for app, v in apps.items():
            if args.app and app != args.app:
                continue
            print(f"## {app}（基准尺寸 {v.get('基准尺寸')}）")
            for k, el in (v.get("元素") or {}).items():
                print(f"   {k:14s} 偏移 {el['偏移']} 锚点 {el.get('锚点')} 命中 {el.get('命中',0)} "
                      f"| {el.get('证据','')[:40]}")
        return 0
    if args.cmd == "learn":
        t = main_window(args.app, args.pid)
        _, _, w, h = rect_of(t)
        mem_put(args.app, args.name, (args.x, args.y), (w, h), args.anchor, args.evidence)
        print(f"[记住] {args.app}.{args.name} 偏移 ({args.x},{args.y}) 锚点 {args.anchor}")
        return 0
    if args.cmd == "where":
        x, y, info = locate(args.app, args.name, args.pid)
        print(f"{args.app}.{args.name} -> ({x},{y}) 来源 {info['来源']}")
        return 0
    if args.cmd == "click":
        return do_click(args.app, args.pid, args.name, args.speed)
    if args.cmd in ("move", "hover"):
        x, y, info = locate(args.app, args.name, args.pid)
        ms = move(x, y, args.speed)
        print(f"[移动] {app_label(args.app, args.name)} -> ({x},{y}) 用时 {ms:.1f}ms")
        return 0
    if args.cmd == "shot":
        p = shot(Path(args.out))
        print(f"[截图] {p}")
        return 0
    if args.cmd == "scan-menu":
        return scan_menu(args.app, args.pid, args.want, args.y0, args.y1, args.step,
                         args.menu_btn, args.dry_run)
    return 2


def app_label(app: str, name: str) -> str:
    return f"{app}.{name}"


if __name__ == "__main__":
    raise SystemExit(main())
