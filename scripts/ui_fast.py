#!/usr/bin/env python3
r"""毫秒级界面原语：直接用 Win32 API，绕开 uiautomation 的 Python 开销。

三件事做到毫秒：
  1. 窗口枚举/前景判断：EnumWindows + GetWindowThreadProcessId（毫秒级，不用 UIA）
  2. 按键：SendInput 批量下发（比 SendKeys 快一个数量级）
  3. 鼠标：SetCursorPos + mouse_event（瞬时）

用法（也可被 ui_daemon.py 常驻调用）:
  python ui_fast.py bench --pid 21156
  python ui_fast.py send --pid 21156 --keys ctrl+alt+q
"""
from __future__ import annotations

import ctypes
import io
import sys
import time
from ctypes import wintypes
from pathlib import Path

if str(getattr(sys.stdout, "encoding", "")).lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)

# ---- 正确的 argtypes，避免 64 位 HWND 被截断（之前踩过的坑）----
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
                               wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]


# ---------------- 窗口 ----------------

def _cls(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 255)
    return buf.value


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def windows(pid: int | None = None) -> list[dict]:
    """可见的顶层窗口（毫秒级）。"""
    out: list[dict] = []

    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if pid is not None and wpid.value != pid:
            return True
        r = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        w, h = r.right - r.left, r.bottom - r.top
        if w < 100 or h < 80:
            return True
        out.append({"hwnd": int(hwnd), "pid": int(wpid.value), "class": _cls(hwnd),
                    "title": _title(hwnd), "rect": (r.left, r.top, r.right, r.bottom),
                    "width": w, "height": h, "area": w * h})
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    return out


PAGE_CLASSES = ("MainWindow", "HomePage")
EXCLUDE = ("MenuWindow", "ToolTip")


def uia_class(hwnd: int) -> str:
    """拿 hwnd 反查 UIA 类名（剪映的 HomePage_QMLTYPE / MainWindow_QMLTYPE 只在 UIA 层暴露）。"""
    try:
        import uiautomation as auto
        return auto.ControlFromHandle(hwnd).ClassName or ""
    except Exception:
        return ""


def main_window(pid: int) -> dict | None:
    """主窗口：先按大小筛，再用 UIA 类名确认（避开独立 MenuWindow）。"""
    best = None
    for w in windows(pid):
        if w["width"] < 400 or w["height"] < 300:
            continue
        cls = uia_class(w["hwnd"])
        if any(x in cls for x in EXCLUDE):
            continue
        if any(x in cls for x in PAGE_CLASSES):
            w = dict(w, uia_class=cls)
            if best is None or w["area"] > best["area"]:
                best = w
    return best


def page_state(pid: int) -> str:
    win = main_window(pid)
    cls = (win or {}).get("uia_class", "")
    if "MainWindow" in cls:
        return "编辑页"
    if "HomePage" in cls:
        return "首页"
    return "未知"


def foreground_pid() -> int:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


kernel32 = ctypes.WinDLL("kernel32")
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001


_WIN_CACHE: dict[int, tuple[float, dict]] = {}
WIN_TTL = 2.0


def cached_main_window(pid: int) -> dict | None:
    hit = _WIN_CACHE.get(pid)
    if hit and time.time() - hit[0] < WIN_TTL:
        return hit[1]
    win = main_window(pid)
    if win:
        _WIN_CACHE[pid] = (time.time(), win)
    return win


def activate(pid: int, timeout: float = 2.0) -> float:
    """抢前台焦点，返回耗时秒数；成功与否看 foreground_pid()。"""
    t0 = time.perf_counter()
    if foreground_pid() == pid:          # 快路径：已经在前台，直接返回
        return time.perf_counter() - t0
    win = cached_main_window(pid)
    if not win:
        return time.perf_counter() - t0
    hwnd = win["hwnd"]
    if foreground_pid() != pid:
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_me = kernel32.GetCurrentThreadId()
        attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True)) if tid_fg and tid_fg != tid_me else False
        try:
            user32.ShowWindow(hwnd, 9)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(tid_me, tid_fg, False)
    return time.perf_counter() - t0


# ---------------- 键鼠 ----------------

VK = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
      "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "tab": 0x09,
      "delete": 0x2E, "del": 0x2E, "backspace": 0x08, "up": 0x26, "down": 0x28,
      "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23,
      "+": 0xBB, "=": 0xBB, "-": 0xBD, "`": 0xC0, "~": 0xC0, ",": 0xBC, ".": 0xBE}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
KEYEVENTF_KEYUP = 0x0002


def parse(seq: str) -> list[int]:
    """'ctrl+shift+z' / 'ctrl+alt+=' / 'Delete' / 'a' → VK 列表。"""
    out = []
    for part in [p.strip() for p in seq.split("+") if p.strip()]:
        low = part.lower()
        if low in VK:
            out.append(VK[low])
        elif len(part) == 1:
            ch = part.upper()
            out.append(ord(ch))
        else:
            raise KeyError(f"不认识的键: {part}")
    return out


def send(seq: str) -> float:
    """一次 SendInput 下发整串按键（按下正序、抬起倒序），返回毫秒。"""
    vks = parse(seq)
    events = []
    for vk in vks:
        events.append(INPUT(type=1, u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0,
                                                                time=0, dwExtraInfo=None))))
    for vk in reversed(vks):
        events.append(INPUT(type=1, u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=0,
                                                                dwFlags=KEYEVENTF_KEYUP,
                                                                time=0, dwExtraInfo=None))))
    arr = (INPUT * len(events))(*events)
    t0 = time.perf_counter()
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    return (time.perf_counter() - t0) * 1000


def move(x: int, y: int, speed: str = "instant") -> float:
    t0 = time.perf_counter()
    steps = {"instant": 1, "fast": 6, "normal": 20}.get(speed, 1)
    if steps == 1:
        user32.SetCursorPos(int(x), int(y))
    else:
        cur = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(cur))
        for i in range(1, steps + 1):
            user32.SetCursorPos(int(cur.x + (x - cur.x) * i / steps),
                                int(cur.y + (y - cur.y) * i / steps))
            time.sleep(0.002)
    return (time.perf_counter() - t0) * 1000


def click(x: int, y: int, speed: str = "instant", guard_pid: int | None = None) -> float:
    if guard_pid is not None and foreground_pid() != guard_pid:
        raise RuntimeError(f"前台不是 pid {guard_pid}，拒绝点击")
    t0 = time.perf_counter()
    move(x, y, speed)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    return (time.perf_counter() - t0) * 1000


def wheel(delta: int, ctrl: bool = False) -> float:
    t0 = time.perf_counter()
    if ctrl:
        user32.keybd_event(0x11, 0, 0, 0)
    for _ in range(abs(delta)):
        user32.mouse_event(0x0800, 0, 0, 120 if delta > 0 else -120, 0)
    if ctrl:
        user32.keybd_event(0x11, 0, 2, 0)
    return (time.perf_counter() - t0) * 1000


# ---------------- 快速抓屏（BitBlt，毫秒级） ----------------

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


gdi32 = ctypes.WinDLL("gdi32")
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]


def grab_fast(box: tuple[int, int, int, int]):
    """抓屏幕指定区域，返回 PIL.Image（BGRA→RGB）。比 ImageGrab 快 3-10 倍。"""
    from PIL import Image
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, hdc, x0, y0, 0x00CC0020 | 0x40000000)   # SRCCOPY|CAPTUREBLT
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h          # 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)
    return img


def grab_png(box: tuple[int, int, int, int], path) -> float:
    t0 = time.perf_counter()
    grab_fast(box).save(path)
    return (time.perf_counter() - t0) * 1000


# ---------------- 截图 ----------------

def shot(path: str | Path, box: tuple[int, int, int, int] | None = None) -> float:
    from PIL import ImageGrab
    p = Path(path)
    t0 = time.perf_counter()
    ImageGrab.grab(bbox=box, all_screens=True).save(p)
    return (time.perf_counter() - t0) * 1000


def bench(pid: int) -> int:
    print(f"[基准] 目标 pid={pid}")
    t0 = time.perf_counter()
    ws = windows()
    print(f"  Win32 枚举所有窗口       : {(time.perf_counter()-t0)*1000:8.2f} ms（{len(ws)} 个）")
    t0 = time.perf_counter()
    mw = main_window(pid)
    print(f"  Win32 找剪映主窗口       : {(time.perf_counter()-t0)*1000:8.2f} ms → {mw['class'] if mw else None}")
    t0 = time.perf_counter()
    st = page_state(pid)
    print(f"  Win32 判断页面状态       : {(time.perf_counter()-t0)*1000:8.2f} ms → {st}")
    t0 = time.perf_counter()
    fg = foreground_pid()
    print(f"  Win32 取前台 pid         : {(time.perf_counter()-t0)*1000:8.2f} ms → {fg}")

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ui_agent as ua
        t0 = time.perf_counter()
        _ = ua.windows(pid)
        print(f"  uiautomation 枚举窗口    : {(time.perf_counter()-t0)*1000:8.2f} ms（对照）")
    except Exception as exc:
        print(f"  （uiautomation 对照失败: {exc}）")

    t0 = time.perf_counter()
    _ = send("ctrl+shift+z")
    print(f"  SendInput 发组合键       : {(time.perf_counter()-t0)*1000:8.2f} ms")
    t0 = time.perf_counter()
    _ = move(900, 500)
    print(f"  SetCursorPos 移动        : {(time.perf_counter()-t0)*1000:8.2f} ms")
    t0 = time.perf_counter()
    from PIL import ImageGrab
    ImageGrab.grab(bbox=(0, 0, 1920, 400), all_screens=True)
    print(f"  区域截图(1920x400)       : {(time.perf_counter()-t0)*1000:8.2f} ms")
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="毫秒级界面原语")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("bench")
    p.add_argument("--pid", type=int, required=True)
    p = sub.add_parser("send")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--keys", required=True)
    p = sub.add_parser("shot")
    p.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.cmd == "bench":
        return bench(args.pid)
    if args.cmd == "send":
        ms = activate(args.pid) * 1000
        if foreground_pid() != args.pid:
            print("[拒执] 拿不到焦点")
            return 3
        ms2 = send(args.keys)
        print(f"[发送] {args.keys}｜抢焦点 {ms:.1f}ms + 发键 {ms2:.2f}ms")
        return 0
    if args.cmd == "shot":
        print(f"[截图] {args.out}｜{shot(args.out):.1f}ms")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
