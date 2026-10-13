"""桌面端常驻的剪映窗口观察面板。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QGroupBox, QSizePolicy,
)


class RealtimeObserverPanel(QWidget):
    """软件启动即观察；只把本地预览与视觉事件呈现在主界面。"""

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        from aicut.desktop_brain import DesktopBrain

        self.brain = DesktopBrain("desktop-gui", interval=1.5, vision=True)
        self.parent_project_root = Path(project_root)
        # 兼容用户把 GUI 项目根目录改到其他位置。
        self.brain.out_dir = Path(project_root) / "work" / "desktop-brain" / "desktop-gui"
        self.brain.out_dir.mkdir(parents=True, exist_ok=True)
        self.brain.latest_frame_file = self.brain.out_dir / "latest.jpg"
        self.brain.events_file = self.brain.out_dir / "events.jsonl"
        self._last_frame_mtime = 0
        self._last_event_count = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        controls = QHBoxLayout()
        self.status = QLabel("观察服务启动中…")
        self.status.setObjectName("muted")
        controls.addWidget(self.status, 1)
        self.btn_toggle = QPushButton("暂停观察")
        self.btn_toggle.clicked.connect(self.toggle_observation)
        controls.addWidget(self.btn_toggle)
        root.addLayout(controls)
        self.model_progress = QLabel('本地模型待命')
        self.model_progress.setWordWrap(True)
        root.addWidget(self.model_progress)

        self.frame = QLabel("等待剪映窗口…\n打开并保持剪映主窗口可见")
        self.frame.setAlignment(Qt.AlignCenter)
        self.frame.setMinimumSize(300, 180)
        self.frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.frame.setStyleSheet("background:#171719;border:1px solid #444;border-radius:6px;")
        root.addWidget(self.frame, 3)

        event_box = QGroupBox("实时分析与观察事件")
        event_layout = QVBoxLayout(event_box)
        self.events = QListWidget()
        event_layout.addWidget(self.events)
        root.addWidget(event_box, 2)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        QTimer.singleShot(1200, self.brain.start)

    def toggle_observation(self):
        if self.brain.state()["running"]:
            self.brain.stop()
            self.btn_toggle.setText("恢复观察")
            self.status.setText("本次运行已暂停；下次启动软件会自动开启")
        else:
            # stop() 关闭 executor；重建引擎才能干净恢复视觉分析。
            from aicut.desktop_brain import DesktopBrain
            self.brain = DesktopBrain("desktop-gui", interval=1.5, vision=True)
            self.brain.out_dir = self.parent_project_root / "work" / "desktop-brain" / "desktop-gui"
            self.brain.out_dir.mkdir(parents=True, exist_ok=True)
            self.brain.latest_frame_file = self.brain.out_dir / "latest.jpg"
            self.brain.events_file = self.brain.out_dir / "events.jsonl"
            self.brain.events = []
            self.brain._event_seq = 0
            self._last_event_count = 0
            self.btn_toggle.setText("暂停观察")
            self.events.clear()
            self.brain.start()

    def set_project_root(self, path: Path):
        self.parent_project_root = Path(path)

    def refresh(self):
        try:
            progress = json.loads((self.parent_project_root / 'work' / 'local-inference-progress.json').read_text(encoding='utf-8'))
            label = {'loading_or_prefill': '加载或读取上下文', 'generating': '正在生成',
                     'complete': '本次调用完成', 'failed': '本次调用失败'}.get(progress.get('state'), '待命')
            elapsed = float(progress.get('elapsed_seconds', 0))
            if progress.get('state') in ('loading_or_prefill', 'generating'):
                elapsed += max(0, time.time() - float(progress.get('updated_at', time.time())))
            self.model_progress.setText(f"模型：{label} · 已返回 {progress.get('response_chars', 0)} 字符 · "
                                        f"耗时 {elapsed:.0f} 秒")
        except (OSError, ValueError, TypeError):
            pass
        state = self.brain.state()
        if state["running"]:
            if state.get("window"):
                title = state["window"].get("title") or "剪映主窗口"
                self.status.setText(f"● 正在观察：{title}　|　视觉分析{'进行中' if state['vision_pending'] else '待命'}")
            else:
                self.status.setText(f"● 观察服务运行中　|　{state.get('error') or '正在查找剪映窗口'}")
        self.btn_toggle.setText("暂停观察" if state["running"] else "恢复观察")

        frame_path = state.get("latest_frame")
        if frame_path:
            try:
                mtime = Path(frame_path).stat().st_mtime_ns
                if mtime != self._last_frame_mtime:
                    self._last_frame_mtime = mtime
                    pix = QPixmap(frame_path)
                    if not pix.isNull():
                        self.frame.setPixmap(pix.scaled(
                            self.frame.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except OSError:
                pass

        with self.brain._lock:
            new_events = self.brain.events[self._last_event_count:]
            self._last_event_count = len(self.brain.events)
        for event in new_events:
            kind = event.get("kind")
            if kind == "screen_frame":
                continue
            if kind == "screen_vision":
                result = event.get("result") or {}
                if isinstance(result, dict):
                    detail = "　".join(f"{k}：{v}" for k, v in result.items())
                else:
                    detail = str(result)
                line = f"视觉分析：{detail}"
            elif kind == "vision_pending":
                line = "视觉模型正在分析最新画面…"
            else:
                line = f"{kind}：{event.get('reason') or json.dumps(event, ensure_ascii=False)}"
            self.events.insertItem(0, line[:500])
        while self.events.count() > 80:
            self.events.takeItem(self.events.count() - 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._last_frame_mtime:
            self.refresh()

    def closeEvent(self, event):
        self._timer.stop()
        self.brain.stop()
        super().closeEvent(event)
