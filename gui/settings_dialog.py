"""设置对话框：工程路径 / 模型 / 剪映 / Ollama / 主题 / AI 接口。"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QPushButton, QHBoxLayout,
    QFileDialog, QComboBox, QDialogButtonBox, QLabel, QGroupBox, QCheckBox,
    QWidget,
)

from .appconfig import GuiConfig


class SettingsDialog(QDialog):
    def __init__(self, cfg: GuiConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("设置")
        self.resize(720, 920)
        lay = QVBoxLayout(self)

        form_box = QGroupBox("路径与依赖")
        form = QFormLayout(form_box)

        self.e_root = QLineEdit(cfg.get("project_root"))
        self.e_py = QLineEdit(cfg.get("python_exe") or "")
        self.e_models = QLineEdit(cfg.get("models_dir"))
        self.e_jy = QLineEdit(cfg.get("jianying_exe"))
        self.e_draft = QLineEdit(cfg.get("draft_root"))
        self.e_out = QLineEdit(cfg.get("output_dir") or "")
        self.e_ollama = QLineEdit(cfg.get("ollama_host"))

        form.addRow("工程根目录", self._with_browse(self.e_root, "dir"))
        form.addRow("Python 解释器(venv)", self._with_browse(self.e_py, "file"))
        form.addRow("模型目录", self._with_browse(self.e_models, "dir"))
        form.addRow("剪映 JianyingPro.exe", self._with_browse(self.e_jy, "file"))
        form.addRow("剪映草稿目录", self._with_browse(self.e_draft, "dir"))
        form.addRow("输出目录", self._with_browse(self.e_out, "dir"))
        form.addRow("Ollama 地址", self.e_ollama)

        lay.addWidget(form_box)

        theme_box = QGroupBox("外观（v5.0.0 皮肤）")
        th = QFormLayout(theme_box)
        self.cmb_theme = QComboBox()
        self.cmb_theme.addItem("C 智能工作台（深色默认）", "workbench")
        self.cmb_theme.addItem("B 极简灵动（浅色）", "simple")
        # 兼容旧 theme 字段：light -> simple
        cur = cfg.get("gui_theme")
        if cur not in ("workbench", "simple"):
            cur = "simple" if (cfg.get("theme", "dark") or "dark") == "light" else "workbench"
        idx = 0 if cur == "workbench" else 1
        self.cmb_theme.setCurrentIndex(idx)
        th.addRow("皮肤", self.cmb_theme)
        lay.addWidget(theme_box)

        ai_box = QGroupBox("AI 大脑 · 付费 API 接口（v5.0.0 多后端）")
        ai = QFormLayout(ai_box)
        self.cmb_strategy = QComboBox()
        self.cmb_strategy.addItem("付费优先（快就替代本地，慢/失败自动回落）", "paid_first")
        self.cmb_strategy.addItem("仅本地（Ollama，免费离线）", "local_only")
        self.cmb_strategy.setCurrentIndex(0)
        self.chk_paid = QCheckBox("允许使用付费 API（关闭后素材绝不出本机）")
        self.chk_paid.setChecked(True)
        ai.addRow("路由策略", self.cmb_strategy)
        ai.addRow("", self.chk_paid)

        # v5.2.0：Copilot 方法论开关（Codex 6 蒸馏：子代理编排 + 偏好学习 + 断点续跑）
        self.chk_copilot = QCheckBox("启用 Copilot 方法论（Codex 蒸馏：三角色子代理编排 + 偏好学习 + 计划断点续跑）")
        self.chk_copilot.setChecked(bool(self.cfg.get("copilot", False)))
        ai.addRow("", self.chk_copilot)

        # 多后端 Key 输入（有 Key 即启用，清空即停用）
        self._PAID_LABELS = {
            # ---- 国内 ----
            "deepseek": "DeepSeek（对话/视觉）",
            "qwen": "通义千问 Qwen（对话）",
            "qwen3-vl-flash": "通义千问 VL（视觉）",
            "deepseek-r1": "DeepSeek R1（推理）",
            "qwen-max": "通义千问 Max（强推理）",
            "glm-4.5": "智谱 GLM-4.5",
            "kimi": "Kimi（月之暗面）",
            # ---- 外网 ----
            "openai": "OpenAI GPT（对话/视觉）",
            "claude": "Anthropic Claude（对话/视觉）",
            "gemini": "Google Gemini（对话/视觉）",
            "groq": "Groq（免费，Llama/Qwen）",
            "openrouter": "OpenRouter（300+ 模型聚合）",
            "mistral": "Mistral AI（对话）",
        }
        self._paid_edits: dict[str, QLineEdit] = {}
        for name, label in self._PAID_LABELS.items():
            e = QLineEdit()
            e.setEchoMode(QLineEdit.Password)
            e.setPlaceholderText("sk-…（留空则停用该通道）")
            self._paid_edits[name] = e
            ai.addRow(label, e)
        ai.addRow("", QLabel("说明：多接口大脑按任务自动路由（视觉/语义/决策/文本）；Key 本地保存于\n"
                             "config/aicut.json，绝不外传；清空某后端 Key 即停用，全空则仅本地。"))
        self._load_ai_config()
        lay.addWidget(ai_box)

        lay.addWidget(self._build_usage_box())

        hint = QLabel("提示：Ollama、whisper 模型、剪映专业版、ffmpeg 均为外部依赖，\n"
                      "首次运行请在「状态检测」里确认；模型不随安装包分发。")
        hint.setObjectName("muted")
        lay.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _build_usage_box(self) -> QGroupBox:
        """AI 用量统计（v5.0.0 P2）：读 work/ai_usage.jsonl 聚合展示。"""
        box = QGroupBox("AI 用量统计（记账 work/ai_usage.jsonl）")
        lay = QVBoxLayout(box)
        self.lbl_usage = QLabel()
        self.lbl_usage.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_usage)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("刷新")
        btn.clicked.connect(self._refresh_usage)
        row.addWidget(btn)
        lay.addLayout(row)
        self._refresh_usage()
        return box

    def _refresh_usage(self) -> None:
        try:
            from aicut.llm_router import usage_summary
            s = usage_summary()
            lines = [
                f"总调用 {s['total_calls']} 次 · 成功 {s['success']} · "
                f"成功率 {s['success_rate'] * 100:.1f}% · 成本估算 ¥{s['total_cost_estimate']:.4f}",
                "",
            ]
            for name, st in sorted(s["by_backend"].items(), key=lambda kv: -kv[1]["calls"]):
                lines.append(
                    f"  {name:<24} {st['calls']} 次 · 成功 {st['ok']} · "
                    f"耗时 {st['seconds']:.1f}s · 成本 ¥{st['cost']:.4f}")
            if not s["by_backend"]:
                lines.append("  （暂无调用记录：首次使用后这里会逐条记账）")
            self.lbl_usage.setText("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self.lbl_usage.setText(f"用量统计读取失败：{exc}")

    def _with_browse(self, edit: QLineEdit, kind: str) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        btn = QPushButton("浏览…")
        def pick():
            if kind == "dir":
                d = QFileDialog.getExistingDirectory(self, "选择目录", edit.text())
            else:
                d, _ = QFileDialog.getOpenFileName(self, "选择文件", edit.text())
            if d:
                edit.setText(d)
        btn.clicked.connect(pick)
        h.addWidget(btn)
        return w

    def apply_to(self, cfg: GuiConfig):
        cfg.set("project_root", self.e_root.text().strip())
        cfg.set("python_exe", self.e_py.text().strip())
        cfg.set("models_dir", self.e_models.text().strip())
        cfg.set("jianying_exe", self.e_jy.text().strip())
        cfg.set("draft_root", self.e_draft.text().strip())
        cfg.set("output_dir", self.e_out.text().strip())
        cfg.set("ollama_host", self.e_ollama.text().strip())
        skin = self.cmb_theme.currentData()
        cfg.set("gui_theme", skin)
        # 同步旧字段，保持向后兼容
        cfg.set("theme", "light" if skin == "simple" else "dark")
        # v5.2.0：Copilot 方法论开关
        cfg.set("copilot", bool(self.chk_copilot.isChecked()))
        self._save_ai_config()

    # ---- AI 多接口配置（config/aicut.json） ----
    def _ai_config_path(self):
        root = self.cfg.get("project_root")
        if not root:
            return None
        return Path(root) / "config" / "aicut.json"

    def _load_ai_config(self):
        p = self._ai_config_path()
        if not p or not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            router = data.get("ai_router", {})
            strategy = router.get("default_strategy", "paid_first")
            idx = 0 if strategy == "paid_first" else 1
            self.cmb_strategy.setCurrentIndex(idx)
            self.chk_paid.setChecked(bool(router.get("global_paid_api_enabled", True)))
            for b in data.get("ai_backends", []):
                name = b.get("name")
                key = b.get("api_key")
                # ${ENV_VAR} 占位符视为未配置，不载入输入框
                if name in self._paid_edits and key and not str(key).startswith("${"):
                    self._paid_edits[name].setText(key)
        except Exception:
            pass  # 配置损坏时保持默认，不打断设置页

    def _save_ai_config(self):
        p = self._ai_config_path()
        if not p:
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            data.setdefault("ai_router", {})
            data["ai_router"]["default_strategy"] = (
                "paid_first" if self.cmb_strategy.currentData() == "paid_first" else "local_only")
            data["ai_router"]["global_paid_api_enabled"] = bool(self.chk_paid.isChecked())
            backends = data.setdefault("ai_backends", [])
            # 模板：name -> (type, base_url, models, vision_model)；新后端按需追加
            _TEMPLATES = {
                "deepseek": ("openai", "https://api.deepseek.com/v1",
                             ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash-vision"],
                             "deepseek-v4-flash-vision"),
                "qwen": ("openai", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                         ["qwen-plus", "qwen-max", "qwen3-vl-plus"], ""),
                "qwen3-vl-flash": ("openai", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                   ["qwen3-vl-flash"], "qwen3-vl-flash"),
                "deepseek-r1": ("openai", "https://api.deepseek.com/v1",
                                ["deepseek-r1"], ""),
                "qwen-max": ("openai", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                             ["qwen-max"], ""),
                "glm-4.5": ("openai", "https://open.bigmodel.cn/api/paas/v4",
                            ["glm-4.5", "glm-4.5-air"], ""),
                "kimi": ("openai", "https://api.moonshot.cn/v1",
                         ["moonshot-v1-128k", "kimi-latest"], ""),
                # ---- 外网（2026-09 核实端点/模型名） ----
                "openai": ("openai", "https://api.openai.com/v1",
                           ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-4.1"], "gpt-5.6-luna"),
                "claude": ("openai", "https://api.anthropic.com/v1",
                           ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"],
                           "claude-sonnet-5"),
                "gemini": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai",
                           ["gemini-2.5-pro", "gemini-2.5-flash"], "gemini-2.5-pro"),
                "groq": ("openai", "https://api.groq.com/openai/v1",
                         ["llama-4-scout-17b-16e-instruct", "qwen3-32b",
                          "llama-3.3-70b-versatile"], "llama-4-scout-17b-16e-instruct"),
                "openrouter": ("openai", "https://openrouter.ai/api/v1",
                               ["openai/gpt-4.1", "anthropic/claude-sonnet-5",
                                "google/gemini-2.5-pro"], "google/gemini-2.5-pro"),
                "mistral": ("openai", "https://api.mistral.ai/v1",
                            ["mistral-large-latest", "mistral-small-latest"], ""),
            }
            for name, edit in self._paid_edits.items():
                key = edit.text().strip()
                if key.startswith("${"):  # 占位符不是真 key，按未配置处理
                    key = ""
                tpl = _TEMPLATES.get(name)
                if not tpl:
                    continue
                btype, base_url, models, vmodel = tpl
                found = next((b for b in backends if b.get("name") == name), None)
                if key:
                    if found is None:
                        found = {"name": name, "type": btype, "base_url": base_url,
                                 "models": models, "enabled": True, "available": True}
                        backends.append(found)
                    found["api_key"] = key
                    found["enabled"] = True
                    found["available"] = True
                    if vmodel:
                        found["vision_model"] = vmodel
                elif found is not None:  # 清空输入 = 移除付费通道
                    found["api_key"] = ""
                    found["enabled"] = False
                    found["available"] = False
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass  # 保存失败不阻断设置流程
