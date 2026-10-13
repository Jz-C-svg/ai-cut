"""主窗口：五区布局 + 菜单/工具栏 + 工作流串联。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QToolBar, QFileDialog, QMessageBox, QLabel,
    QProgressBar, QWidget, QVBoxLayout, QHBoxLayout, QApplication,
    QPushButton, QMenu, QDockWidget, QInputDialog,
)

from .appconfig import GuiConfig, VERSION, APP_DISPLAY, is_frozen, app_icon_path
from . import theme as theme_mod
from .panels import MaterialPanel, PreviewPanel, ParamPanel, BottomPanel
from .workers import PipelineWorker, StatusCheckWorker
from .settings_dialog import SettingsDialog
from .env_wizard import EnvWizard
from .template_dialog import TemplateClipDialog
from .realtime_observer import RealtimeObserverPanel


class MainWindow(QMainWindow):
    def __init__(self, cfg: GuiConfig):
        super().__init__()
        self.cfg = cfg
        self.worker: PipelineWorker | None = None
        self._template_dialog: TemplateClipDialog | None = None
        # 五步流水线状态：-1=未开始，0-4=当前步
        self._pipeline_step = -1
        self._pipeline_done: set[int] = set()
        self._set_window_title()
        self.setWindowIcon(QIcon(app_icon_path()))
        self.resize(1440, 900)
        self.setMinimumSize(1280, 800)
        self.showMaximized()

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()
        self._apply_theme()

        # 实时观察是桌面工具常驻能力：默认随 GUI 启动并展示在右侧停靠区。
        self.observer_dock = QDockWidget("实时观察与分析", self)
        self.observer_dock.setObjectName("realtimeObserverDock")
        self.observer_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.observer_panel = RealtimeObserverPanel(self.cfg.project_root, self)
        self.observer_panel.set_project_root(self.cfg.project_root)
        self.observer_dock.setWidget(self.observer_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self.observer_dock)
        self.observer_dock.setMinimumWidth(340)
        self.observer_dock.resize(420, 600)

        # 加载风格
        self._load_styles()
        # 启动后刷新素材与状态
        QTimer.singleShot(300, self.material.reload_from_manifest)
        QTimer.singleShot(400, self.check_status)
        # 首次启动自动弹环境配置向导
        if not self.cfg.get("env_wizard_completed", False):
            QTimer.singleShot(500, self.open_env_wizard)

    def _set_window_title(self):
        self.setWindowTitle(f"{APP_DISPLAY} v{VERSION}  ·  多软件交付工作台")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # 左
        self.material = MaterialPanel(self.cfg)
        self.material.import_requested.connect(self.import_materials)
        self.material.selected.connect(self._on_material_selected)

        # 中
        self.preview = PreviewPanel()

        # 右
        self.params = ParamPanel(self.cfg)
        self.params.preview_tts.connect(self.on_tts_preview)

        top = QSplitter(Qt.Horizontal)
        top.addWidget(self.material)
        top.addWidget(self.preview)
        top.addWidget(self.params)
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 1)
        top.setStretchFactor(2, 0)
        top.setSizes([300, 720, 340])
        self.top_splitter = top

        self.bottom = BottomPanel()

        root = QSplitter(Qt.Vertical)
        root.addWidget(top)
        root.addWidget(self.bottom)
        root.setStretchFactor(0, 3)
        root.setStretchFactor(1, 1)
        self.setCentralWidget(root)

    def _build_menu(self):
        mb = self.menuBar()
        # 文件
        m_file = mb.addMenu("文件(&F)")
        a_import = QAction("导入素材…", self)
        a_import.triggered.connect(self.import_materials)
        m_file.addAction(a_import)
        a_opendraft = QAction("打开剪映草稿目录", self)
        a_opendraft.triggered.connect(lambda: self._open_path(self.cfg.get("draft_root")))
        m_file.addAction(a_opendraft)
        m_file.addSeparator()
        a_quit = QAction("退出", self)
        a_quit.triggered.connect(self.close)
        m_file.addAction(a_quit)

        # 编辑
        m_edit = mb.addMenu("编辑(&E)")
        a_pref = QAction("首选项…", self)
        a_pref.triggered.connect(self.open_settings)
        m_edit.addAction(a_pref)

        # 工具
        m_tools = mb.addMenu("工具(&T)")
        a_analyze = QAction("开始分析", self)
        a_analyze.triggered.connect(self.start_analysis)
        m_tools.addAction(a_analyze)
        a_edit = QAction("一键成片", self)
        a_edit.triggered.connect(self.start_edit)
        m_tools.addAction(a_edit)
        a_quality = QAction("画面质检", self)
        a_quality.triggered.connect(self.start_quality)
        m_tools.addAction(a_quality)
        a_tmpl = QAction("模板化剪辑…", self)
        a_tmpl.triggered.connect(self.open_template_dialog)
        m_tools.addAction(a_tmpl)

        # 设置
        m_set = mb.addMenu("设置(&S)")
        a_wizard = QAction("环境配置向导", self)
        a_wizard.triggered.connect(self.open_env_wizard)
        m_set.addAction(a_wizard)
        a_status = QAction("环境状态检测", self)
        a_status.triggered.connect(self.check_status)
        m_set.addAction(a_status)
        a_set2 = QAction("首选项…", self)
        a_set2.triggered.connect(self.open_settings)
        m_set.addAction(a_set2)

        # 帮助
        m_help = mb.addMenu("帮助(&H)")
        a_about = QAction("关于", self)
        a_about.triggered.connect(self.about)
        m_help.addAction(a_about)

    def _build_toolbar(self):
        tb = QToolBar("主工具栏", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        # Logo
        logo = QLabel("  AI 智能剪辑  ")
        logo.setObjectName("logo")
        tb.addWidget(logo)
        # 五步流水线灯
        self.pipeline_widget = self._build_pipeline_indicator()
        tb.addWidget(self.pipeline_widget)
        tb.addSeparator()

        def act(text, slot):
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        self.tb_import = act("📁 导入素材", self.import_materials)
        self.tb_analyze = act("🔍 开始分析", self.start_analysis)
        self.tb_template = act("🧠 模板化剪辑", self.open_template_dialog)
        self.tb_quality = act("✅ 画面质检", self.start_quality)
        self.tb_cancel = act('停止任务', self.cancel_task)
        self.tb_cancel.setEnabled(False)
        tb.addSeparator()
        self.tb_jy = act("📽 打开剪映", self.open_jianying)
        self.tb_set = act("⚙ 设置", self.open_settings)

        # 右侧：大按钮 + 皮肤切换
        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(6, 0, 6, 0)
        rl.setSpacing(8)
        self.btn_hero = QPushButton("⚡ 智能成片")
        self.btn_hero.setObjectName("primary")
        self.btn_hero.clicked.connect(self.start_edit)
        rl.addWidget(self.btn_hero)
        self.tb_edit = self.btn_hero  # 兼容 _set_busy 引用
        self.btn_theme = QPushButton("🎨")
        self.btn_theme.setObjectName("iconBtn")
        self.btn_theme.setToolTip("切换皮肤：C 智能工作台 / B 极简灵动")
        tmenu = QMenu(self)
        a_c = QAction("C 智能工作台（深色）", self)
        a_c.triggered.connect(lambda: self._switch_theme("workbench"))
        a_b = QAction("B 极简灵动（浅色）", self)
        a_b.triggered.connect(lambda: self._switch_theme("simple"))
        tmenu.addAction(a_c)
        tmenu.addAction(a_b)
        self.btn_theme.setMenu(tmenu)
        rl.addWidget(self.btn_theme)
        tb.addWidget(right)

    # -- 五步流水线灯 --
    PIPELINE_NAMES = ["感知", "理解", "比对", "模板", "成片"]

    def _build_pipeline_indicator(self) -> QWidget:
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(6)
        self.pipeline_labels: list[QLabel] = []
        for i, name in enumerate(self.PIPELINE_NAMES):
            lb = QLabel(f"● {name}")
            lb.setObjectName("pipelineStep")
            lay.addWidget(lb)
            self.pipeline_labels.append(lb)
            if i < len(self.PIPELINE_NAMES) - 1:
                sep = QLabel("→")
                sep.setObjectName("muted")
                lay.addWidget(sep)
        self._refresh_pipeline()
        return host

    def _refresh_pipeline(self):
        c = theme_mod.palette(self._resolve_theme())
        for i, lb in enumerate(self.pipeline_labels):
            if i in self._pipeline_done:
                lb.setStyleSheet(f"color:{c['ok']}; font-weight:bold;")
            elif i == self._pipeline_step:
                lb.setStyleSheet(f"color:{c['accent2']}; font-weight:bold;")
            else:
                lb.setStyleSheet(f"color:{c['muted']};")

    # -- 皮肤切换 --
    def _resolve_theme(self) -> str:
        gt = self.cfg.get("gui_theme")
        if gt in ("workbench", "simple"):
            return gt
        if (self.cfg.get("theme", "dark") or "dark") == "light":
            return "simple"
        return "workbench"

    def _switch_theme(self, theme: str):
        self.cfg.set("gui_theme", theme)
        self.cfg.save()
        self._apply_theme()
        self._apply_layout_mode(theme)
        self._sync_project_config(theme)
        self._refresh_pipeline()
        self.bottom.append_log(
            f"已切换皮肤：{'C 智能工作台' if theme == 'workbench' else 'B 极简灵动'}", "ok")

    def _apply_layout_mode(self, theme: str):
        if theme_mod.is_simple(theme):
            # B 模式：侧栏收起，中央大画布为主（仍可拖动分隔条展开）
            self.top_splitter.setSizes([0, 1000, 0])
            self.preview.set_ai_status("拖入视频，自动出片")
        else:
            self.top_splitter.setSizes([300, 720, 340])

    def _sync_project_config(self, theme: str):
        """同步写入工程 config/aicut.json 的 gui_theme 字段（容错）。"""
        try:
            p = self.cfg.project_root / "config" / "aicut.json"
            data = {}
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
            data["gui_theme"] = theme
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _build_statusbar(self):
        self.sb_progress = QProgressBar()
        self.sb_progress.setMaximumWidth(240)
        self.sb_progress.setValue(0)
        self.sb_stage = QLabel("就绪")
        self.sb_stage.setObjectName("muted")
        self.sb_env = QLabel("")
        self.sb_env.setObjectName("muted")
        # v5.0.0 增强：流程状态灯 + 版本 + 当前模型
        self.sb_pipe = QLabel("●理解 ○规划 ○执行 ○质检")
        self.sb_pipe.setObjectName("muted")
        try:
            project_config = json.loads((self.cfg.project_root / 'config' / 'aicut.json').read_text(encoding='utf-8-sig'))
            model = (project_config.get('vision') or {}).get('model') or '未配置'
        except (OSError, ValueError):
            model = '配置待检查'
        self.sb_ver = QLabel(f"v{VERSION}  ·  模型 {model}")
        self.sb_ver.setObjectName("muted")
        sb = self.statusBar()
        sb.addWidget(self.sb_stage, 1)
        sb.addPermanentWidget(self.sb_pipe)
        sb.addPermanentWidget(self.sb_ver)
        sb.addPermanentWidget(self.sb_env)
        sb.addPermanentWidget(self.sb_progress)

    def _apply_theme(self):
        theme = self._resolve_theme()
        self.setStyleSheet(theme_mod.build_qss(theme))
        # 启动时按皮肤应用布局
        self._apply_layout_mode(theme)

    # ------------------------------------------------------------------ 风格
    def _load_styles(self):
        sp = self.cfg.content_library / "styles.json"
        if sp.exists():
            try:
                data = __import__("json").loads(sp.read_text(encoding="utf-8"))
                self.params.load_styles(data.get("styles", []))
            except Exception as e:
                self.bottom.append_log(f"读取 styles.json 失败: {e}", "warn")

    # ------------------------------------------------------------------ 素材
    def import_materials(self):
        start_dir = self.cfg.get("last_material_dir") or str(self.cfg.project_root)
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择素材视频", start_dir,
            "视频文件 (*.mp4 *.mov *.MOV *.MP4 *.mkv *.avi);;所有文件 (*.*)")
        if not files:
            return
        self.cfg.set("last_material_dir", str(Path(files[0]).parent))
        self.cfg.save()
        self.material.load_materials(files)
        self.bottom.append_log(f"已导入 {len(files)} 个素材", "ok")
        if files:
            self.preview.load_video(files[0])

    def _on_material_selected(self, item: dict):
        v = item.get("video") or item.get("source")
        if v and Path(v).exists():
            self.preview.load_video(v)
        name = item.get("name", "")
        # v5.0.0：刷新决策单（理解 / 参考模板）与 AI 标记层
        try:
            self.params.refresh_understanding(name)
            self.params.refresh_benchmark(name)
        except Exception:
            pass
        self._load_markers_for(name)

    def _load_markers_for(self, name: str):
        """从 semantic-{name}.json 读高光候选生成 AI 标记层。"""
        markers = []
        data = None
        try:
            p = self.cfg.project_root / "work" / f"semantic-{name}.json"
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if data:
            hooks = data.get("高光候选") or []
            icons = ["⚡", "💡", "⏰"]
            for i, h in enumerate(hooks[:3]):
                if isinstance(h, dict):
                    t = h.get("t", 0)
                    mm = int(t) // 60
                    ss = int(t) % 60
                    markers.append({
                        "icon": icons[i % 3],
                        "time": f"{mm:02d}:{ss:02d}",
                        "text": h.get("理由", "高光点"),
                    })
        self.bottom.set_markers(markers)

    # ------------------------------------------------------------------ 工作流
    def _selected_video(self) -> str | None:
        it = self.material.current_item()
        if not it:
            QMessageBox.warning(self, "提示", "请先在左侧选择一个素材")
            return None
        v = it.get("video")
        if not v or not Path(v).exists():
            v = it.get("source")
        return v

    def start_analysis(self):
        it = self.material.current_item()
        if not it:
            QMessageBox.warning(self, "提示", "请先在左侧选择一个素材")
            return
        source = it.get("source") or it.get("video")
        if not source:
            QMessageBox.warning(self, "提示", "该素材缺少来源路径")
            return
        self._launch_worker("analyze", {"source": source})

    def start_edit(self):
        v = self._selected_video()
        if not v:
            return
        payload = {"video": v, **self.params.collect()}
        self._launch_worker("edit", payload)

    def start_quality(self):
        v = self._selected_video()
        if not v:
            return
        self._launch_worker("quality", {"target": v})

    def on_tts_preview(self):
        p = self.params.collect()
        self._launch_worker("tts_preview",
                            {"voiceover": p["voiceover"], "voice": p["voice"]})

    # ------------------------------------------------------------------ 模板化剪辑（v3.8.0）
    def open_template_dialog(self):
        if self._template_dialog is None:
            self._template_dialog = TemplateClipDialog(self, self)
        self._template_dialog.show()
        self._template_dialog.raise_()
        self._template_dialog.activateWindow()
        self._template_dialog._sync_material()

    def run_template_task(self, mode: str, payload: dict, on_done):
        """模板化剪辑流程专用的 worker 启动器：复用 PipelineWorker，结果回调给对话框。

        on_done(ok: bool, result: str) —— result 为输出 JSON 路径或草稿目录。
        """
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "已有任务在运行，请等待完成")
            return
        self.sb_progress.setValue(0)
        self.worker = PipelineWorker(self.cfg, mode, payload, self)
        self.worker.log.connect(self.bottom.append_log)
        self.worker.stage.connect(self._on_stage)
        self.worker.progress.connect(self._on_progress)
        self.worker.material_ready.connect(self._on_material_ready)

        def _finished(ok: bool, result: str):
            self._set_busy(False)
            if ok:
                self.sb_progress.setValue(100)
                self.sb_stage.setText("完成")
            else:
                self.sb_stage.setText("失败")
            try:
                on_done(ok, result)
            except Exception as e:  # noqa: BLE001
                self.bottom.append_log(f"模板流程回调异常: {e}", "err")

        self.worker.finished.connect(_finished)
        self.worker.start()
        self._set_busy(True)

    # ------------------------------------------------------------------ worker
    def _launch_worker(self, mode: str, payload: dict):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "已有任务在运行，请等待完成")
            return
        self.sb_progress.setValue(0)
        self.worker = PipelineWorker(self.cfg, mode, payload, self)
        self.worker.log.connect(self.bottom.append_log)
        self.worker.stage.connect(self._on_stage)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.material_ready.connect(self._on_material_ready)
        self.worker.start()
        self._set_busy(True)

    def cancel_task(self):
        if self.worker and self.worker.isRunning():
            self.worker.abort()
            self.tb_cancel.setEnabled(False)

    def _set_busy(self, busy: bool):
        self.tb_cancel.setEnabled(busy)
        for a in (self.tb_analyze, self.tb_edit, self.tb_quality, self.tb_template):
            a.setEnabled(not busy)

    # 阶段名 -> 流水线步骤索引
    STAGE_MAP = {
        '素材检查与预处理': 0, '语音转写': 0,
        '镜头分析': 1, '语义分析': 1,
        '学习库检索与剪辑决策': 2, '生成前质量门': 2,
        '生成可恢复草稿': 3, 'Copilot 复审': 3,
        '草稿回读校验': 4, '基础 MP4 导出': 4, '复制复审后草稿到剪映': 4,
        "素材归一化": 0, "语音转写（Whisper）": 0,
        "镜头视觉档案（Ollama）": 1, "语义档案": 1,
        "分析素材指纹": 2, "联网找参考": 2,
        "套用模板出片": 3, "模板化剪辑": 3,
        "一键成片": 4,
    }
    STAGE_AI_STATUS = {
        "素材归一化": "正在归一化素材…",
        "语音转写（Whisper）": "语音转写中…",
        "镜头视觉档案（Ollama）": "正在抽取镜头…",
        "语义档案": "正在理解语义…",
        "分析素材指纹": "正在分析指纹…",
        "联网找参考": "正在比对参考…",
        "套用模板出片": "正在套用模板…",
        "一键成片": "正在合成成片…",
        "画面质检": "正在质检画面…",
        "配音试听": "正在合成配音…",
    }

    def _on_stage(self, name: str):
        self.sb_stage.setText(f"⏳ {name}…")
        self.bottom.append_log(f"—— 阶段：{name} ——", "stage")
        idx = self.STAGE_MAP.get(name)
        if idx is not None:
            self._pipeline_step = idx
            for i in range(idx):
                self._pipeline_done.add(i)
            self._refresh_pipeline()
        self.preview.set_ai_status(self.STAGE_AI_STATUS.get(name, f"{name}…"))

    def _on_progress(self, pct: int, stage: str):
        self.sb_progress.setValue(pct)
        if stage:
            self.sb_stage.setText(stage)

    def _on_material_ready(self, item: dict):
        self.bottom.append_log(
            f"归一化完成：{item.get('name')}  时长 {item.get('duration')}s", "ok")

    def _on_finished(self, ok: bool, result: str):
        self._set_busy(False)
        if ok and result.startswith('PLAN:'):
            import hashlib
            report = json.loads(Path(result[5:]).read_text(encoding='utf-8'))
            sheet_path = Path(report['artifacts']['working_sheet'])
            sheet_bytes = sheet_path.read_bytes()
            sheet = json.loads(sheet_bytes.decode('utf-8-sig'))
            digest = hashlib.sha256(sheet_bytes).hexdigest()
            self.sb_stage.setText('方案待确认')
            self.sb_pipe.setText('●理解 ●规划 ○执行 ○质检')
            self.preview.set_ai_status('方案待确认，尚未生成草稿')
            self.bottom.append_log(f'方案已保存：{sheet_path}', 'ok')
            from aicut.editor_capabilities import EDITORS
            choices = list(EDITORS)
            labels = [EDITORS[k] for k in choices]
            label, selected = QInputDialog.getItem(self, '选择交付软件', '目标剪辑软件', labels, 0, False)
            if not selected:
                return
            software = choices[labels.index(label)]
            modes = ['原生草稿（待软件打开验收）', '直接输出 MP4（FFmpeg 引擎）'] if software in {'jianying', 'capcut'} else ['直接输出 MP4（FFmpeg 引擎）']
            mode, selected = QInputDialog.getItem(self, '选择交付方式',
                'MP4 使用独立渲染引擎；其他软件原生自动导出仍待接入。', modes, 0, False)
            if not selected:
                return
            engine = 'project' if mode.startswith('原生') else 'portable'
            box = QMessageBox(self)
            box.setWindowTitle('确认剪辑方案')
            box.setText(f'目标：{label}\n交付：{mode}\n请展开详情检查方案，再确认执行。')
            details = json.dumps(sheet, ensure_ascii=False, indent=2)
            intent_path = sheet.get('intent_path')
            if intent_path and Path(intent_path).is_file():
                from aicut.edit_intent import intent_decision_sheet
                details = intent_decision_sheet(json.loads(Path(intent_path).read_text(encoding='utf-8')))
            box.setDetailedText(details)
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.button(QMessageBox.Yes).setText('确认并执行')
            box.button(QMessageBox.No).setText('暂不生成')
            box.setDefaultButton(QMessageBox.No)
            publish = bool(self.worker and self.worker.payload.get('publish', True))
            if box.exec() == QMessageBox.Yes:
                QTimer.singleShot(100, lambda: self._launch_worker('build_approved',
                    {'sheet': str(sheet_path), 'sha256': digest, 'publish': publish,
                     'software': software, 'engine': engine}))
            return
        if ok and result.startswith('DELIVERY:'):
            report_path = Path(result[len('DELIVERY:'):])
            report = json.loads(report_path.read_text(encoding='utf-8'))
            rendered = report.get('status') == 'rendered_pending_visual_review'
            self.sb_progress.setValue(100)
            text = 'MP4 已输出并通过解码检查，待画面复核' if rendered else '草稿已生成，待目标软件验收'
            self.sb_stage.setText(text)
            self.sb_pipe.setText('●理解 ●规划 ●交付 ○视觉验收')
            self.preview.set_ai_status(text)
            self.bottom.append_log(text + '；报告：' + str(report_path), 'ok')
            for name, path in report.get('artifacts', {}).items():
                self.bottom.append_log(f'{name}: {path}', 'ok')
            if rendered and report.get('artifacts', {}).get('video'):
                self.preview.load_video(report['artifacts']['video'])
            return
        if ok:
            self.sb_progress.setValue(100)
            self.sb_stage.setText("完成")
            self.bottom.append_log(f"✔ 完成：{result}", "ok")
            if self._pipeline_step >= 0:
                self._pipeline_done.add(self._pipeline_step)
                self._pipeline_step = -1
                self._refresh_pipeline()
            self.sb_pipe.setText("●理解 ●规划 ●执行 ●质检")
            self.preview.set_ai_status("完成 ✓")
            if self.worker and self.worker.mode == 'build_approved':
                self.sb_stage.setText('草稿已生成，待成片复核')
                self.sb_pipe.setText('●理解 ●规划 ●草稿 ○成片复核')
                self.preview.set_ai_status('草稿已生成；导出效果尚未验收')
            if self.worker and self.worker.mode == 'build_approved' and self.worker.payload.get('publish'):
                self.bottom.append_log("已发布到剪映草稿目录，可点「打开剪映」查看。", "ok")
        else:
            self.sb_stage.setText("失败")
            self.bottom.append_log(f"✘ 失败：{result}", "err")
            QMessageBox.warning(self, "任务失败", result)

    # ------------------------------------------------------------------ 状态
    def check_status(self):
        self.sb_env.setText("正在检测环境…")
        w = StatusCheckWorker(self.cfg, self)
        w.done.connect(self._on_status)
        w.start()
        self._status_worker = w  # 保持引用

    def _on_status(self, res: dict):
        names = {"ollama": "Ollama", "whisper": "Whisper", "jianying": "剪映",
                 "ffmpeg": "FFmpeg", "edge_tts": "网络", "python": "Python"}
        ok_cnt = sum(1 for v in res.values() if v["ok"])
        self.sb_env.setText(f"环境 {ok_cnt}/{len(res)} 就绪")
        for k, v in res.items():
            mark = "✔" if v["ok"] else "⚠"
            self.bottom.append_log(f"{mark} {names.get(k,k)}: {v['detail']}",
                                   "ok" if v["ok"] else "warn")
        # Ollama 未启动时提示
        if not res["ollama"]["ok"]:
            ret = QMessageBox.question(
                self, "Ollama 未运行",
                "未检测到 Ollama 服务（127.0.0.1:11434）。\n"
                "AI 视觉分析需要它。是否现在尝试后台启动 Ollama？",
                QMessageBox.Yes | QMessageBox.No)
            if ret == QMessageBox.Yes:
                try:
                    subprocess.Popen(["ollama", "serve"],
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self.bottom.append_log("已发起 ollama serve，稍候重试检测", "cmd")
                    QTimer.singleShot(3000, self.check_status)
                except Exception as e:
                    self.bottom.append_log(f"启动失败：{e}（请手动启动 Ollama）", "err")

    # ------------------------------------------------------------------ 其它
    def open_jianying(self):
        p = self.cfg.get("jianying_exe")
        if p and Path(p).exists():
            try:
                os.startfile(p)  # type: ignore[attr-defined]
            except Exception as e:
                QMessageBox.warning(self, "失败", str(e))
        else:
            QMessageBox.warning(self, "未找到剪映", "请在设置中配置 JianyingPro.exe 路径")

    def _open_path(self, path: str):
        if path and Path(path).exists():
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            QMessageBox.warning(self, "路径不存在", str(path))

    def open_env_wizard(self):
        w = EnvWizard(self.cfg, self)
        w.exec()
        self.bottom.append_log("环境配置向导已完成", "ok")
        self.check_status()

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            dlg.apply_to(self.cfg)
            self.cfg.save()
            self._apply_theme()
            self._sync_project_config(self._resolve_theme())
            self._load_styles()
            self.bottom.append_log("设置已保存", "ok")

    def about(self):
        QMessageBox.about(
            self, f"关于 {APP_DISPLAY}",
            f"<b>{APP_DISPLAY}</b> v{VERSION}<br><br>"
            "AI 智能剪辑桌面工具：本地 Ollama + Whisper + Edge-TTS，<br>"
            "最终产物为剪映专业版明文草稿，在剪映中导出。<br><br>"
            "全程免费、本地优先。")

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            self.worker.abort()
            if not self.worker.wait(8000):
                e.ignore()
                return
        self.observer_panel.brain.stop()
        self.cfg.save()
        super().closeEvent(e)
