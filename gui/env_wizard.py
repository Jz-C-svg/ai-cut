"""首次运行环境配置向导（QDialog + QStackedWidget 自定义分步）。

左侧步骤列表高亮当前步，右侧显示该步检测结果与引导按钮；
底部上一步/下一步/完成。完成后写 env_wizard_completed 到
工程 config/aicut.json 与 GUI 配置。
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QListWidget, QListWidgetItem, QStackedWidget,
    QWidget, QFrame,
)

from .appconfig import GuiConfig, is_frozen


# ---------------------------------------------------------------------------
# 检测函数
# ---------------------------------------------------------------------------
def check_ollama(host: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        n = len(data.get("models", []))
        return True, f"服务在线（已发现 {n} 个模型）"
    except Exception:
        return False, "未检测到 Ollama 服务（默认 127.0.0.1:11434）"


def check_whisper(models_dir: str) -> tuple[bool, str]:
    d = Path(models_dir)
    if not d.exists():
        return False, f"目录不存在：{d}"
    if list(d.glob("*faster-whisper*")):
        return True, f"已就绪：{d}"
    return False, f"未在 {d} 找到 faster-whisper 模型"


def check_jianying(exe: str) -> tuple[bool, str]:
    if exe and Path(exe).exists():
        return True, f"已找到：{exe}"
    return False, "未自动识别到剪映专业版，请手动定位 JianyingPro.exe"


def check_ffmpeg() -> tuple[bool, str]:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=5)
        first = (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else ""
        return out.returncode == 0, first[:70] or "ffmpeg 可用"
    except Exception:
        return False, "ffmpeg 不在系统 PATH"


def check_network() -> tuple[bool, str]:
    try:
        urllib.request.urlopen("https://www.baidu.com", timeout=3)
        return True, "网络正常（Edge-TTS 在线配音可用）"
    except Exception:
        return False, "无法联网，在线配音功能将不可用"


def check_python(py: str) -> tuple[bool, str]:
    if py and Path(py).is_file():
        return True, f"解释器就绪：{py}"
    return False, f"未找到解释器：{py}"


# ---------------------------------------------------------------------------
# 单步页
# ---------------------------------------------------------------------------
class StepWidget(QWidget):
    def __init__(self, title: str, desc: str, cfg: GuiConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._passed = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)

        head = QLabel(title)
        head.setStyleSheet("font-size:17px; font-weight:bold; color:#ffffff;")
        lay.addWidget(head)
        sub = QLabel(desc)
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#aaaaaa; margin-bottom:8px;")
        lay.addWidget(sub)

        self.status = QLabel("尚未检测")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            "font-size:14px; padding:12px; background:#2d2d30; border-radius:6px; color:#ddd;")
        lay.addWidget(self.status)

        self.row = QHBoxLayout()
        self.btn_retry = QPushButton("重新检测")
        self.btn_retry.clicked.connect(self.run_check)
        self.row.addWidget(self.btn_retry)
        self.row.addStretch(1)
        lay.addLayout(self.row)
        lay.addStretch(1)

    def do_check(self) -> tuple[bool, str]:
        raise NotImplementedError

    def run_check(self):
        ok, detail = self.do_check()
        self._passed = ok
        mark = "✅ 通过" if ok else "⚠ 未就绪"
        color = "#4ec9b0" if ok else "#d7ba00"
        self.status.setText(f'<span style="color:{color};font-weight:bold">{mark}</span><br>{detail}')
        return ok

    def _add_btn(self, text, slot):
        b = QPushButton(text)
        b.clicked.connect(slot)
        self.row.addWidget(b)
        return b


class OllamaStep(StepWidget):
    def __init__(self, cfg, parent=None):
        super().__init__("第 1 步 / 共 6 步 · Ollama 服务",
                         "AI 视觉分析依赖本地 Ollama（127.0.0.1:11434）。", cfg, parent)
        self._add_btn("一键启动 ollama serve", self.start_ollama)
        self._add_btn("打开下载页", lambda: os.startfile("https://ollama.com"))  # type: ignore

    def do_check(self):
        return check_ollama(self.cfg.get("ollama_host", "http://127.0.0.1:11434"))

    def start_ollama(self):
        try:
            subprocess.Popen(["ollama", "serve"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.status.setText("已发起启动，3 秒后复检…")
            QTimer.singleShot(3000, self.run_check)
        except Exception as e:
            self.status.setText(f"⚠ 启动失败：{e}（请手动安装并启动 Ollama）")


class WhisperStep(StepWidget):
    def __init__(self, cfg, parent=None):
        super().__init__("第 2 步 / 共 6 步 · Whisper 模型",
                         "语音转写需要 faster-whisper small 模型文件。", cfg, parent)
        self._add_btn("选择模型目录…", self.browse)

    def do_check(self):
        return check_whisper(self.cfg.get("models_dir", ""))

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择模型目录（含 faster-whisper）",
                                             self.cfg.get("models_dir", ""))
        if d:
            self.cfg.set("models_dir", d)
            self.run_check()


class JianyingStep(StepWidget):
    def __init__(self, cfg, parent=None):
        super().__init__("第 3 步 / 共 6 步 · 剪映专业版",
                         "成片最终发布到剪映草稿目录，请定位 JianyingPro.exe。", cfg, parent)
        self._add_btn("选择 JianyingPro.exe…", self.browse)

    def do_check(self):
        return check_jianying(self.cfg.get("jianying_exe", ""))

    def browse(self):
        f, _ = QFileDialog.getOpenFileName(self, "定位 JianyingPro.exe",
                                           self.cfg.get("jianying_exe", ""),
                                           "JianyingPro.exe (JianyingPro.exe)")
        if f:
            self.cfg.set("jianying_exe", f)
            self.run_check()


class FfmpegStep(StepWidget):
    def __init__(self, cfg, parent=None):
        super().__init__("第 4 步 / 共 6 步 · FFmpeg",
                         "素材归一化与缩略图依赖 ffmpeg/ffprobe（需在系统 PATH）。", cfg, parent)

    def do_check(self):
        return check_ffmpeg()


class NetworkStep(StepWidget):
    def __init__(self, cfg, parent=None):
        super().__init__("第 5 步 / 共 6 步 · 网络（Edge-TTS）",
                         "在线配音需要访问微软 Edge-TTS 服务。", cfg, parent)

    def do_check(self):
        return check_network()


class PythonStep(StepWidget):
    def __init__(self, cfg, parent=None):
        super().__init__("第 6 步 / 共 6 步 · Python 运行库",
                         "GUI 通过工程 venv 解释器执行分析与出片脚本。", cfg, parent)
        self._add_btn("选择后端 venv python.exe…", self.browse)

    def do_check(self):
        return check_python(self.cfg.python_exe())

    def browse(self):
        f, _ = QFileDialog.getOpenFileName(self, "定位 venv python.exe",
                                           str(self.cfg.python_exe()),
                                           "python.exe (python.exe)")
        if f:
            self.cfg.set("python_exe", f)
            self.run_check()


# ---------------------------------------------------------------------------
# 向导对话框
# ---------------------------------------------------------------------------
class EnvWizard(QDialog):
    def __init__(self, cfg: GuiConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("环境配置向导")
        self.setMinimumSize(760, 520)
        self.setStyleSheet(
            "QDialog { background:#1e1e1e; }"
            "QListWidget { background:#252526; color:#ddd; border:none; font-size:13px; }"
            "QListWidget::item { padding:8px 10px; }"
            "QListWidget::item:selected { background:#0078d4; color:white; }"
            "QPushButton { background:#2d2d30; color:#eee; border:1px solid #3f3f46;"
            " border-radius:4px; padding:6px 16px; min-width:80px; }"
            "QPushButton:hover { background:#0078d4; color:white; }"
        )

        root = QHBoxLayout(self)

        # 左：步骤列表
        self.listw = QListWidget()
        self.listw.setFixedWidth(190)
        for name in ("Ollama 服务", "Whisper 模型", "剪映专业版",
                     "FFmpeg", "网络配音", "Python 运行库"):
            QListWidgetItem(name, self.listw)
        root.addWidget(self.listw)

        # 右：堆叠页
        right = QVBoxLayout()
        self.stack = QStackedWidget()
        self.steps = [
            OllamaStep(cfg), WhisperStep(cfg), JianyingStep(cfg),
            FfmpegStep(cfg), NetworkStep(cfg), PythonStep(cfg),
        ]
        for s in self.steps:
            self.stack.addWidget(s)
        right.addWidget(self.stack, 1)

        # 底部按钮
        bar = QHBoxLayout()
        self.btn_prev = QPushButton("上一步")
        self.btn_next = QPushButton("下一步")
        self.btn_skip = QPushButton("跳过")
        self.btn_finish = QPushButton("完成")
        bar.addWidget(self.btn_skip)
        bar.addStretch(1)
        bar.addWidget(self.btn_prev)
        bar.addWidget(self.btn_next)
        bar.addWidget(self.btn_finish)
        right.addLayout(bar)
        root.addLayout(right, 1)

        self.listw.currentRowChanged.connect(self.goto)
        self.btn_prev.clicked.connect(lambda: self.goto(self.stack.currentIndex() - 1))
        self.btn_next.clicked.connect(lambda: self.goto(self.stack.currentIndex() + 1))
        self.btn_skip.clicked.connect(self.reject)
        self.btn_finish.clicked.connect(self._on_finish)

        self.listw.setCurrentRow(0)

    def goto(self, idx: int):
        idx = max(0, min(idx, len(self.steps) - 1))
        self.stack.setCurrentIndex(idx)
        self.listw.blockSignals(True)
        self.listw.setCurrentRow(idx)
        self.listw.blockSignals(False)
        self.btn_prev.setEnabled(idx > 0)
        self.btn_next.setEnabled(idx < len(self.steps) - 1)
        # 进入该页即检测
        QTimer.singleShot(150, self.steps[idx].run_check)

    def _on_finish(self):
        self.cfg.set("env_wizard_completed", True)
        self.cfg.save()
        try:
            proj = self.cfg.project_root / "config" / "aicut.json"
            data = json.loads(proj.read_text(encoding="utf-8")) if proj.exists() else {}
            data["env_wizard_completed"] = True
            data.setdefault("ollama_host", self.cfg.get("ollama_host"))
            data["jianying_path"] = self.cfg.get("jianying_exe")
            data["python_path"] = self.cfg.python_exe()
            proj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        self.accept()
