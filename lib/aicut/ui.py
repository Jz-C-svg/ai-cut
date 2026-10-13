r"""界面通道：驱动剪映本体的那一半"手"。

原则：
  1. 工程文件能做的事不点界面（稳），所以这里只做工程做不到的：花字、云字体、
     特效库、模板、导出、以及"人已经在剪映里剪了一半"的接管。
  2. 键位白名单：不在表里的一律拒发（Ctrl+Q、Alt+F4 这种会掉数据的绝不碰）。
  3. 每次都校验前台进程是不是剪映，不是就拒执——防止把按键发给别的窗口。
  4. 剪映没开时明确报错，不要假装成功。

底层复用 v1.7 的毫秒级原语（ui_fast：EnumWindows / SendInput / SetCursorPos /
ImageGrab），不重复实现。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from . import messages
from . import speed as _speed
from .paths import SCRIPTS, WORK, python_exe

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ui_fast as F  # noqa: E402

def _set_dpi_awareness() -> None:
    """Call before any coordinate API; otherwise clicks shift under high DPI."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass


_set_dpi_awareness()


EXE_HINT = "jianyingpro"

# 只允许这些键（来自 ui_do.py 记录的本机快捷键方案）
ALLOWED = {
    "e", "d", "q", "w", "space", "up", "down", "left", "right", "delete", "enter", "esc",
    "ctrl+z", "ctrl+shift+z", "ctrl+c", "ctrl+x", "ctrl+v", "ctrl+a", "ctrl+b", "ctrl+e",
    "ctrl+m", "ctrl+f", "ctrl+shift+k", "ctrl+alt+q", "ctrl+alt+=", "ctrl+alt+-",
    "shift+left", "shift+right",
}
FORBIDDEN = {"ctrl+q", "alt+f4", "ctrl+alt+delete"}

_PID_CACHE: tuple[float, int | None] = (0.0, None)


def _proc_path(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def find_pid(force: bool = False) -> int | None:
    """找到剪映主窗口所属进程号；10 秒内复用结果。"""
    global _PID_CACHE
    now = time.time()
    if not force and now - _PID_CACHE[0] < 10 and _PID_CACHE[1]:
        return _PID_CACHE[1]
    best: tuple[int, int] | None = None  # (area, pid)
    for win in F.windows():
        if win["width"] < 400 or win["height"] < 300:
            continue
        cls = F.uia_class(win["hwnd"])
        if any(x in cls for x in F.EXCLUDE):
            continue
        is_page = any(x in cls for x in F.PAGE_CLASSES)
        if not is_page and not win["class"].startswith("Qt"):
            continue
        if EXE_HINT not in _proc_path(win["pid"]).lower():
            continue
        score = win["area"] + (10 ** 7 if is_page else 0)
        if best is None or score > best[0]:
            best = (score, win["pid"])
    pid = best[1] if best else None
    _PID_CACHE = (now, pid)
    return pid


def _win(pid: int) -> dict | None:
    """带缓存的主窗口：枚举一次约 0.88ms，缓存后几乎为 0。"""
    return F.cached_main_window(pid) or _win(pid)


def cursor() -> tuple[int, int]:
    """当前光标位置（标定落点误差用）。"""
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y)


def info(pid: int | None = None) -> dict:
    """剪映现在是什么状态（不开也能安全调用）。"""
    pid = pid or find_pid()
    if not pid:
        return {"running": False, "pid": None, "page": "未运行"}
    win = _win(pid)
    if not win:
        return {"running": False, "pid": pid, "page": "无主窗口"}
    return {
        "running": True,
        "pid": pid,
        "page": F.page_state(pid),
        "uia_class": win.get("uia_class", ""),
        "rect": win["rect"],
        "width": win["width"],
        "height": win["height"],
        "title": win["title"],
    }


def require(pid: int | None = None) -> int:
    pid = pid or find_pid()
    if not pid:
        raise RuntimeError("剪映没开：界面通道需要先启动剪映专业版")
    return pid


def activate(pid: int | None = None) -> float:
    return F.activate(require(pid))


def launch(wait: float = 45.0) -> dict:
    """定位并启动剪映专业版（Apps 下最新版本目录），等待主窗口出现。

    v5.0.0（P1 自检/体验优化）：剪映没开时自动拉起；已开则直接返回现状。
    返回 {"running", "pid", "launched", "seconds", "error"}。
    """
    import os
    import subprocess
    if find_pid():
        return {"running": True, "pid": find_pid(), "launched": False,
                "seconds": 0.0, "error": ""}
    apps = Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro" / "Apps"
    if not apps.is_dir():
        return {"running": False, "pid": None, "launched": False,
                "seconds": 0.0, "error": "未找到剪映安装目录"}
    vers = sorted(p for p in apps.iterdir() if (p / "JianyingPro.exe").is_file())
    if not vers:
        return {"running": False, "pid": None, "launched": False,
                "seconds": 0.0, "error": "未找到 JianyingPro.exe"}
    exe = vers[-1] / "JianyingPro.exe"
    subprocess.Popen([str(exe)])
    t0 = time.time()
    while time.time() - t0 < wait:
        pid = find_pid(force=True)
        if pid:
            return {"running": True, "pid": pid, "launched": True,
                    "seconds": round(time.time() - t0, 1), "error": ""}
        time.sleep(1.0)
    return {"running": False, "pid": None, "launched": True,
            "seconds": round(time.time() - t0, 1), "error": "启动超时"}


def key(seq: str, pid: int | None = None, delay: float | None = None) -> dict:
    seq = "".join(seq.split()).lower()
    if delay is None:
        delay = float(_speed.get("key_delay_s"))
    if seq in FORBIDDEN:
        raise RuntimeError(f"拒绝执行危险按键：{seq}")
    if seq not in ALLOWED:
        raise RuntimeError(f"按键 {seq} 不在白名单里；要加请改 lib/aicut/ui.py 的 ALLOWED")
    pid = require(pid)
    F.activate(pid)
    if F.foreground_pid() != pid:
        raise RuntimeError("前台不是剪映，拒发按键")
    cost = F.send(seq)
    time.sleep(delay)
    return {"keys": seq, "cost_ms": round(cost * 1000, 2), "pid": pid}


def click(x: int, y: int, pid: int | None = None, speed: str = "auto") -> dict:
    pid = require(pid)
    F.activate(pid)
    win = _win(pid)
    if not win:
        raise RuntimeError("拿不到剪映主窗口")
    if speed == "auto":
        speed = str(_speed.get("click_speed"))
    left, top, _, _ = win["rect"]
    if F.foreground_pid() != pid:
        raise RuntimeError(messages.FOREGROUND_MISMATCH)
    target = (left + int(x), top + int(y))
    t0 = time.perf_counter()
    F.move(*target, speed=speed)
    time.sleep(float(_speed.get("post_move_ms", 8)) / 1000.0)  # 移动与点击之间留一点，避免点到旧位置
    user = ctypes.windll.user32
    user.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(float(_speed.get("click_press_ms", 6)) / 1000.0)
    user.mouse_event(0x0004, 0, 0, 0, 0)
    cost = (time.perf_counter() - t0) * 1000
    return {"x": x, "y": y, "speed": speed, "cost_ms": round(cost, 2), "pid": pid}


def wheel(delta: int, ctrl: bool = False, pid: int | None = None) -> dict:
    pid = require(pid)
    F.activate(pid)
    return {"cost_ms": round(F.wheel(int(delta), ctrl=ctrl) * 1000, 2), "pid": pid}


def shot(path: str | Path | None = None, pid: int | None = None,
         region: tuple[int, int, int, int] | None = None) -> Path:
    """截剪映主窗口（默认整窗）。"""
    pid = require(pid)
    win = _win(pid)
    if not win:
        raise RuntimeError("拿不到剪映主窗口")
    out = Path(path) if path else WORK / "ui" / f"ui-{int(time.time() * 1000)}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    box = region or win["rect"]
    F.shot(out, box=box)
    return out


def open_draft(name: str, pid: int | None = None, timeout: int = 90) -> dict:
    """在剪映首页双击打开草稿（用窗口类变化做确定性校验）。"""
    import os
    import subprocess

    pid = require(pid)
    proc = subprocess.run(
        [python_exe(), str(SCRIPTS / "open_draft.py"), "--pid", str(pid)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    page = F.page_state(pid)
    return {"rc": proc.returncode, "page": page, "opened": page == "编辑页",
            "stdout_tail": (proc.stdout or "")[-300:], "stderr_tail": (proc.stderr or "")[-200:]}


def refresh_jianying(timeout: int = 180) -> dict:
    """重启剪映让它扫到新草稿（会优雅退出并重开，属于重操作）。"""
    import os
    import subprocess

    proc = subprocess.run(
        [python_exe(), str(SCRIPTS / "jianying_refresh.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    _PID_CACHE = (0.0, None)
    return {"rc": proc.returncode, "stdout_tail": (proc.stdout or "")[-400:],
            "stderr_tail": (proc.stderr or "")[-200:]}


def export(timeout: float | None = None) -> dict:
    """触发剪映导出（Ctrl+M，本机方案）。是否能完成取决于剪映的导出面板。"""
    if timeout is None:
        timeout = float(_speed.get("page_settle_s"))
    pid = require()
    before = shot(WORK / "ui" / "before-export.png", pid=pid)
    key("ctrl+m", pid=pid, delay=timeout)
    after = shot(WORK / "ui" / "after-export.png", pid=pid)
    from . import verify

    check = verify.check_screen_changed(before, after, min_ratio=0.01)
    return {"pid": pid, "changed": check.ok, "detail": check.actual,
            "before": str(before), "after": str(after)}
