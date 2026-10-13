"""AI 智能剪辑 · 桌面 GUI 入口。

源码运行：
    set PYTHONPATH=<project-root>\\lib
    <project-root>\\venv\\Scripts\\python.exe -m gui.main
或：
    <project-root>\\venv\\Scripts\\python.exe gui\\main.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap():
    """把工程 lib 目录加入 sys.path，保证源码/打包后都能 import aicut。"""
    if getattr(sys, "frozen", False):
        return
    here = Path(__file__).resolve().parent          # .../gui
    root = here.parent                              # <project-root>
    lib = root / "lib"
    if lib.exists() and str(lib) not in sys.path:
        sys.path.insert(0, str(lib))


def _spawn_warmup() -> None:
    """后台线程预热本地模型（qwen2.5vl 视觉 + qwen3 文本），不阻塞 UI 启动。

    v5.0.0（P1 冷启动优化）：把"首载等待"从用户剪辑时转移到程序启动时。
    """
    import threading

    def _run() -> None:
        try:
            from aicut.llm import warmup_models
            warmup_models(also_text=True)
        except Exception:  # noqa: BLE001 —— 预热失败不影响主流程
            pass

    threading.Thread(target=_run, daemon=True).start()


def main() -> int:
    _bootstrap()
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import importlib
        mod = importlib.import_module("gui.main")
        return mod.main()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("AIcut")
    app.setOrganizationName("AIcut")

    from .appconfig import GuiConfig
    from .mainwindow import MainWindow

    cfg = GuiConfig.load()
    win = MainWindow(cfg)
    win.show()
    _spawn_warmup()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
