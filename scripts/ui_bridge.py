#!/usr/bin/env python3
r"""剪辑软件操作桥：识别软件窗口、截图给 AI 看、按快捷键执行操作。

设计原则（安全第一）：
- 只对指定软件的前台窗口操作；窗口不在前台时拒绝执行
- 支持 --dry-run 只打印不执行、--step 每步等回车
- 每步之间留延迟，随时可按 Esc 中止

用法:
    python ui_bridge.py windows                       # 列出可见窗口
    python ui_bridge.py see --app 剪映专业版           # 截图并让 AI 描述界面状态
    python ui_bridge.py press --app 剪映专业版 --keys ctrl+b
    python ui_bridge.py run --app 剪映专业版 --steps "ctrl+b,delete,ctrl+z" [--dry-run] [--step]
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import uiautomation as auto  # noqa: E402

PROFILES = ROOT / "config" / "software_profiles.json"
SHOT_DIR = ROOT / "work" / "ui"


def load_profiles() -> dict:
    return json.loads(PROFILES.read_text(encoding="utf-8"))


def profile(name: str) -> dict:
    data = load_profiles()
    if name in data and isinstance(data[name], dict):
        return data[name]
    for k, v in data.items():
        if isinstance(v, dict) and name.lower() in k.lower():
            return v
    raise KeyError(f"没有这个软件档案: {name}（可用: " + ", ".join(k for k, v in data.items() if isinstance(v, dict)) + "）")


def enum_windows() -> list:
    out = []
    for w in auto.GetRootControl().GetChildren():
        try:
            r = w.BoundingRectangle
            out.append({"control": w, "name": (w.Name or "").strip(), "class": w.ClassName or "",
                        "pid": w.ProcessId, "area": max(0, r.width()) * max(0, r.height()),
                        "width": r.width(), "height": r.height()})
        except Exception:
            continue
    return out


def find_target(name: str, pid: int | None = None, title: str | None = None) -> dict:
    """找目标主窗口。

    安全约定：给了 --pid 就只认这个进程；给了 --title 就要求标题完全一致。
    绝不因为"进程名相同"就操作一个不是你启动的窗口。
    """
    prof = profile(name)
    for item in enum_windows():
        if pid and item["pid"] != pid:
            continue
        if title and item["name"] != title:
            continue
        if pid or title:
            if item["width"] < 200 or item["height"] < 150:
                continue
            return item
    if pid or title:
        raise RuntimeError(f"没找到匹配 pid={pid} title={title} 的窗口")
    procs = [str(prof.get("进程", "")).lower()]
    keys = [str(k).lower() for k in (prof.get("标题关键词") or [])]
    best = None
    for item in enum_windows():
        title = item["name"].lower()
        pid = item["pid"]
        proc_name = ""
        try:
            out = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                  f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName"],
                                 capture_output=True, text=True, timeout=10).stdout.strip().lower()
            proc_name = out
        except Exception:
            pass
        match = (proc_name and proc_name in procs) or any(k and k in title for k in keys)
        if not match:
            continue
        if item["width"] < 400 or item["height"] < 300:
            continue
        if best is None or item["area"] > best["area"]:
            best = item
    if not best:
        raise RuntimeError(f"没找到 {name} 的主窗口（请先打开软件，且窗口不要最小化）")
    return best

def shot_of(target: dict, tag: str = "ui") -> Path:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"{tag}-{time.strftime('%H%M%S')}.png"
    if not capture_window(target["control"], path):
        target["control"].CaptureToImage(str(path))
    return path


def describe(target: dict, question: str | None = None) -> str:
    import understand
    img = base64.b64encode(shot_of(target).read_bytes()).decode()
    prompt = question or ("这是视频剪辑软件的界面截图。请用 JSON 回答："
                          '{"软件":"...","界面状态":"当前界面（草稿列表/编辑页/导出面板等）",'
                          '"可见元素":["最多6个按钮或面板"],"建议操作":["针对当前状态，下一步该按哪些键"]}')
    return understand.vision_cloud(img, understand.load_key(), prompt)


NAMED_KEYS = {"delete": "Delete", "enter": "Enter", "tab": "Tab", "space": "Space", "esc": "Escape",
              "escape": "Escape", "backspace": "Backspace", "home": "Home", "end": "End",
              "pageup": "PageUp", "pagedown": "PageDown", "up": "Up", "down": "Down",
              "left": "Left", "right": "Right", "insert": "Insert", "f12": "F12"}


def to_sendkeys(spec: str) -> str:
    """把可读写法转成 uiautomation 的按键语法。

    示例：ctrl+shift+z -> {Ctrl}{Shift}z   delete -> {Delete}   hello -> hello（直接输入文字）
    已经是 {Ctrl}s 这种原始写法的原样返回。
    """
    if spec.startswith("{"):
        return spec
    if "+" not in spec and spec.lower() not in NAMED_KEYS:
        return spec                      # 普通文本，直接输入
    parts = [x.strip() for x in spec.split("+") if x.strip()]
    out = ""
    for part in parts[:-1]:
        low = part.lower()
        if low in ("ctrl", "control"):
            out += "{Ctrl}"
        elif low in ("shift",):
            out += "{Shift}"
        elif low in ("alt",):
            out += "{Alt}"
        elif low in ("win", "cmd", "super"):
            out += "{Win}"
    last = parts[-1]
    low = last.lower()
    out += "{" + NAMED_KEYS[low] + "}" if low in NAMED_KEYS else last
    return out


def press(target: dict, keys: str, delay: float = 0.4) -> None:
    """只在前台窗口是目标软件时发送按键。"""
    ctrl = target["control"]
    try:
        if not ctrl.IsTopLevel():
            pass
    except Exception:
        pass
    ctrl.SetActive()
    time.sleep(0.35)
    focused = auto.GetForegroundControl()
    if focused is None or focused.ProcessId != ctrl.ProcessId:
        raise RuntimeError(f"前台窗口不是目标软件（当前前台 pid={getattr(focused,'ProcessId',None)}），已拒绝发送按键")
    seq = to_sendkeys(keys)
    auto.SendKeys(seq, waitTime=0.05)
    time.sleep(delay)


def main() -> int:
    ap = argparse.ArgumentParser(description="剪辑软件操作桥")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("windows", help="列出可见窗口")
    p1.add_argument("--min-area", type=int, default=0)

    p2 = sub.add_parser("see", help="截图并让 AI 描述界面")
    p2.add_argument("--app", required=True)
    p2.add_argument("--ask", help="自定义提问")
    p2.add_argument("--pid", type=int, help="只操作这个进程")

    p3 = sub.add_parser("press", help="发送快捷键")
    p3.add_argument("--app", required=True)
    p3.add_argument("--keys", required=True)
    p3.add_argument("--repeat", type=int, default=1)
    p3.add_argument("--delay", type=float, default=0.5)
    p3.add_argument("--dry-run", action="store_true")
    p3.add_argument("--pid", type=int, help="只操作这个进程")

    p4 = sub.add_parser("run", help="按序列执行操作")
    p4.add_argument("--app", required=True)
    p4.add_argument("--steps", required=True, help="用逗号分隔，如 ctrl+b,delete,ctrl+z")
    p4.add_argument("--delay", type=float, default=0.6)
    p4.add_argument("--dry-run", action="store_true")
    p4.add_argument("--step", action="store_true", help="每步等回车确认")
    p4.add_argument("--pid", type=int, help="只操作这个进程")

    args = ap.parse_args()

    if args.cmd == "windows":
        rows = [w for w in enum_windows() if w["area"] >= args.min_area and w["name"]]
        rows.sort(key=lambda x: -x["area"])
        print(f"{'窗口标题':44s} {'类名':26s} {'尺寸':>11s}  pid")
        for w in rows[:25]:
            print(f"  {w['name'][:42]:42s} {w['class'][:24]:24s} {w['width']:>5d}x{w['height']:<5d} {w['pid']}")
        return 0

    if args.cmd == "see":
        t = find_target(args.app, pid=getattr(args, 'pid', None))
        print(f"目标: {t['name']} | {t['width']}x{t['height']} | pid {t['pid']}")
        print("--- AI 识别 ---")
        print(describe(t, args.ask))
        return 0

    if args.cmd == "press":
        t = find_target(args.app, pid=getattr(args, 'pid', None))
        print(f"目标: {t['name']} | pid {t['pid']}")
        if args.dry_run:
            print(f"[试运行] 将发送 {args.keys} × {args.repeat}，间隔 {args.delay}s（未执行）")
            return 0
        for i in range(args.repeat):
            press(t, args.keys, args.delay)
            print(f"  已发送 {args.keys} -> {to_sendkeys(args.keys)} ({i+1}/{args.repeat})")
        return 0

    if args.cmd == "run":
        t = find_target(args.app, pid=getattr(args, 'pid', None))
        steps = [s.strip() for s in args.steps.split(",") if s.strip()]
        print(f"目标: {t['name']} | pid {t['pid']} | 共 {len(steps)} 步")
        for i, s in enumerate(steps, 1):
            if args.dry_run:
                print(f"  [{i}/{len(steps)}] {s}（试运行，未执行）")
                continue
            if args.step:
                input(f"  按回车执行第 {i} 步：{s}")
            press(t, s, args.delay)
            print(f"  [{i}/{len(steps)}] 已执行 {s} -> {to_sendkeys(s)}")
        print("完成。若误操作请立即在软件里按 Ctrl+Z 撤销。")
        return 0

    return 2


# ---------- 窗口截图（PrintWindow + 自写 PNG，零依赖，可抓被遮挡的窗口）----------
import struct
import zlib
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# 64 位句柄必须声明为 c_void_p，否则指针会被截断成 32 位
user32.GetWindowDC.restype = ctypes.c_void_p
user32.GetWindowDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
user32.PrintWindow.restype = wintypes.BOOL
user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


def _write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def capture_window(control, path: Path) -> bool:
    """用 PrintWindow 抓窗口自身内容（即使被其他窗口遮挡也能抓对）。"""
    hwnd = control.NativeWindowHandle
    if not hwnd:
        return False
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w < 50 or h < 50:
        return False

    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    data = bytearray(buf.raw)
    data[3::4] = b"\xff" * (w * h)  # 强制不透明
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_png(path, w, h, bytes(data))

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return bool(ok) and path.stat().st_size > 3000


if __name__ == "__main__":
    raise SystemExit(main())
