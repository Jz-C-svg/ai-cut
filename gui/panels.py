"""五区面板：左侧素材 / 中间预览 / 右侧参数 / 底部时间线+日志。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, QSize
from PySide6.QtGui import QIcon, QPixmap, QPalette, QColor, QDesktopServices
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QTabWidget, QGroupBox, QCheckBox, QSlider, QComboBox,
    QTextEdit, QLineEdit, QSplitter, QProgressBar, QFrame, QScrollArea, QSizePolicy,
    QToolTip,
)

from .appconfig import GuiConfig


def _read_json_safe(p: Path) -> dict | None:
    try:
        if p.exists():
            import json as _json
            return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _material_score(work_dir: Path, name: str) -> float | None:
    """尝试从 shotprof-{name}.json 读取 AI 质量评分，失败返回 None。"""
    if not name:
        return None
    data = _read_json_safe(work_dir / f"shotprof-{name}.json")
    if not isinstance(data, dict):
        return None
    for key in ("quality_score", "overall_score", "quality", "score", "grade"):
        v = data.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    shots = data.get("shots")
    if isinstance(shots, list) and shots:
        vals = []
        for s in shots:
            if not isinstance(s, dict):
                continue
            for key in ("quality_score", "score", "quality"):
                v = s.get(key)
                if isinstance(v, (int, float)):
                    vals.append(float(v))
                    break
        if vals:
            return round(sum(vals) / len(vals), 1)
    return None


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def extract_thumbnail(video_path: str, out_jpg: Path) -> Path | None:
    """用 ffmpeg 抽第 1 秒帧作缩略图。"""
    try:
        if out_jpg.exists():
            return out_jpg
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "1", "-i", video_path, "-vframes", "1",
             "-vf", "scale=160:-1", str(out_jpg)],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out_jpg if out_jpg.exists() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 左侧素材面板
# ---------------------------------------------------------------------------
class MaterialPanel(QWidget):
    selected = Signal(dict)       # 选中素材 manifest 项或 {path}
    import_requested = Signal()

    def __init__(self, cfg: GuiConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.items: list[dict] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        title = QLabel("素材库")
        title.setObjectName("h1")
        lay.addWidget(title)

        bar = QHBoxLayout()
        self.btn_import = QPushButton("＋ 导入素材")
        self.btn_import.clicked.connect(self.import_requested.emit)
        bar.addWidget(self.btn_import)
        self.score_badge = QLabel("★--")
        self.score_badge.setObjectName("aiBadge")
        bar.addStretch(1)
        bar.addWidget(self.score_badge)
        lay.addLayout(bar)

        self.listw = QListWidget()
        self.listw.setIconSize(QSize(120, 68))
        self.listw.setMinimumWidth(280)
        self.listw.currentRowChanged.connect(self._on_select)
        lay.addWidget(self.listw, 1)

        self.info = QLabel("尚未导入素材。\n\n点击「导入素材」选择视频文件，\n随后用顶部「开始分析」跑 AI 分析。")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        self.info.setFrameShape(QFrame.StyledPanel)
        lay.addWidget(self.info)

    def load_materials(self, paths: list[str]):
        self.listw.clear()
        self.items = []
        thumbs = self.cfg.thumbnail_dir()
        work_dir = self.cfg.project_root / "work"
        for p in paths:
            p = Path(p)
            it = {"name": p.stem, "source": str(p), "duration": None}
            self.items.append(it)
            sc = _material_score(work_dir, p.stem)
            score_txt = f"  ★{sc:.1f}" if sc is not None else "  ★--"
            row = QListWidgetItem(p.stem + score_txt)
            tj = thumbs / (p.stem + ".jpg")
            thumb = extract_thumbnail(str(p), tj)
            if thumb:
                row.setIcon(QIcon(str(thumb)))
            row.setData(Qt.UserRole, it)
            self.listw.addItem(row)

    def reload_from_manifest(self):
        """读取 work/manifest.json 列出已归一化素材。"""
        mp = self.cfg.project_root / "work" / "manifest.json"
        if not mp.exists():
            return
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            return
        self.listw.clear()
        self.items = []
        thumbs = self.cfg.thumbnail_dir()
        work_dir = self.cfg.project_root / "work"
        for it in data.get("items", []):
            if it.get("name", "").startswith("selftest"):
                continue
            self.items.append(it)
            sc = _material_score(work_dir, it.get("name", ""))
            score_txt = f"  ★{sc:.1f}" if sc is not None else "  ★--"
            label = f'{it.get("name")}  ({it.get("duration", "-")}s)' + score_txt
            row = QListWidgetItem(label)
            v = it.get("video")
            if v and Path(v).exists():
                tj = thumbs / (it.get("name") + ".jpg")
                thumb = extract_thumbnail(v, tj)
                if thumb:
                    row.setIcon(QIcon(str(thumb)))
            row.setData(Qt.UserRole, it)
            self.listw.addItem(row)

    def _label(self, p: Path) -> str:
        return p.stem

    def _on_select(self, row: int):
        if row < 0 or row >= len(self.items):
            return
        it = self.items[row]
        dur = it.get("duration")
        lines = [f"名称：{it.get('name')}",
                 f"时长：{dur}s" if dur else "时长：未知",
                 f"来源：{Path(it.get('source','')).name}"]
        self.info.setText("\n".join(lines))
        self._update_score_badge(it)
        self.selected.emit(it)

    def _update_score_badge(self, it: dict):
        from . import theme as _t
        c = _t.palette(self.cfg.get("gui_theme", "workbench"))
        sc = _material_score(self.cfg.project_root / "work", it.get("name", ""))
        if sc is not None:
            self.score_badge.setText(f"★ {sc:.1f}")
            self.score_badge.setStyleSheet(
                f"background-color:{c['ok']}; color:#102a2a;"
                f" border-radius:9px; padding:2px 10px; font-size:11px; font-weight:bold;")
        else:
            self.score_badge.setText("★ --")
            self.score_badge.setStyleSheet(
                f"background-color:{c['chip']}; color:{c['muted']};"
                f" border-radius:9px; padding:2px 10px; font-size:11px;")

    def current_item(self) -> dict | None:
        row = self.listw.currentRow()
        if 0 <= row < len(self.items):
            return self.items[row]
        return None


# ---------------------------------------------------------------------------
# 中间预览面板
# ---------------------------------------------------------------------------
class PreviewPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        self.video = QVideoWidget()
        self.video.setMinimumHeight(360)
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.placeholder = QLabel("🎬  预览区\n\n导入并选中素材后，在此播放。\n出片完成后可直接查看效果。")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("muted")
        self.placeholder.setStyleSheet("border:1px dashed #555; border-radius:8px; font-size:16px;")

        stack = QVBoxLayout()
        stack.addWidget(self.placeholder, 1)
        self.video.hide()
        lay.addWidget(self.video, 1)
        lay.addWidget(self.placeholder, 1)

        # 控制条
        ctrl = QHBoxLayout()
        self.btn_play = QPushButton("▶ 播放")
        self.btn_pause = QPushButton("⏸ 暂停")
        self.btn_play.clicked.connect(self.play)
        self.btn_pause.clicked.connect(self.pause)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setEnabled(False)
        ctrl.addWidget(self.btn_play)
        ctrl.addWidget(self.btn_pause)
        ctrl.addWidget(self.slider, 1)
        lay.addLayout(ctrl)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(
            lambda pos: self.slider.setValue(int(pos)) if not self.slider.isSliderDown() else None)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, int(d)))
        self.slider.sliderMoved.connect(lambda v: self.player.setPosition(v))

        # AI 视角小窗（画中画，右上角悬浮）
        self.ai_overlay = QLabel("👁 AI 视角\n待机中", self)
        self.ai_overlay.setObjectName("aiOverlay")
        self.ai_overlay.setFixedSize(160, 120)
        self.ai_overlay.setAlignment(Qt.AlignCenter)
        self.ai_overlay.show()
        self._place_ai_overlay()

    def _place_ai_overlay(self):
        m = 10
        self.ai_overlay.move(self.width() - self.ai_overlay.width() - m, m)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_ai_overlay()

    def set_ai_status(self, text: str):
        self.ai_overlay.setText(f"👁 AI 视角\n{text}")

    def load_video(self, path: str):
        if not path or not Path(path).exists():
            return
        self.placeholder.hide()
        self.video.show()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.slider.setEnabled(True)

    def play(self):
        if self.player.source().isEmpty():
            return
        self.player.play()

    def pause(self):
        self.player.pause()


# ---------------------------------------------------------------------------
# 右侧参数面板
# ---------------------------------------------------------------------------
VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓（女声·温柔）"),
    ("zh-CN-XiaoyiNeural", "晓伊（女声·活泼）"),
    ("zh-CN-YunjianNeural", "云健（男声·浑厚）"),
    ("zh-CN-YunxiNeural", "云希（男声·青年）"),
    ("zh-CN-YunyangNeural", "云扬（男声·专业播报）"),
    ("zh-CN-XiaochenNeural", "晓辰（女声·知性）"),
]


class ParamPanel(QWidget):
    changed = Signal()
    preview_tts = Signal()

    def __init__(self, cfg: GuiConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.styles: list[dict] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self._build_understand_tab()
        self._build_style_tab()
        self._build_visual_tab()
        self._build_benchmark_tab()
        self._build_voice_tab()
        self._build_camera_tab()
        self._build_advanced_tab()
        self._build_usage_tab()
        self.setMinimumWidth(320)

    # -- 理解页（v5.0.0 决策单）--
    def _build_understand_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.und_text = QTextEdit()
        self.und_text.setReadOnly(True)
        self.und_text.setPlaceholderText("选中素材并完成 AI 分析后，\n此处显示类型 / 镜头数 / 叙事摘要…")
        v.addWidget(self.und_text, 1)
        self.tabs.addTab(page, "理解")

    def refresh_understanding(self, name: str):
        """从 work/understand-{name}.json 或 semantic-{name}.json 读取理解结果。"""
        work = self.cfg.project_root / "work"
        data = (_read_json_safe(work / f"understand-{name}.json")
                or _read_json_safe(work / f"semantic-{name}.json"))
        if not data:
            self.und_text.setHtml(
                "<i style='color:#888'>暂无数据 —— 请先对该素材执行「开始分析」。</i>")
            return
        parts = []
        vtype = (data.get("video_type") or data.get("适合结构")
                 or data.get("类型") or "未识别")
        shots = data.get("shots")
        n_shots = len(shots) if isinstance(shots, list) else (data.get("镜头数") or "-")
        parts.append(f"<b>类型：</b>{vtype}<br><b>镜头数：</b>{n_shots}")
        summary = data.get("叙事摘要")
        if isinstance(summary, list) and summary:
            parts.append("<b>叙事摘要：</b><br>" + "<br>".join(f"· {x}" for x in summary))
        points = data.get("关键信息点")
        if isinstance(points, list) and points:
            parts.append("<b>关键信息点：</b><br>" + "<br>".join(f"· {x}" for x in points))
        for k, label in (("主角", "主角"), ("场景", "场景"), ("情绪基调", "情绪基调")):
            if data.get(k):
                parts.append(f"<b>{label}：</b>{data.get(k)}")
        self.und_text.setHtml("<br><br>".join(parts))

    # -- 参考模板页（v5.0.0 决策单）--
    def _build_benchmark_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.bench_text = QTextEdit()
        self.bench_text.setReadOnly(True)
        self.bench_text.setPlaceholderText("完成「找参考 / 比对」后，\n此处显示 Top 参考与相似度…")
        v.addWidget(self.bench_text, 1)
        self.tabs.addTab(page, "参考模板")

    def refresh_benchmark(self, name: str):
        """从 work/benchmark-result-{name}.json 读取比对结果。"""
        work = self.cfg.project_root / "work"
        data = _read_json_safe(work / f"benchmark-result-{name}.json")
        if not data:
            self.bench_text.setHtml(
                "<i style='color:#888'>暂无数据 —— 执行「模板化剪辑」后生成比对结果。</i>")
            return
        lines = []
        # 尝试多种可能的结构
        refs = (data.get("top_refs") or data.get("references")
                or data.get("results") or [])
        if isinstance(data.get("best_ref"), dict):
            refs = [data["best_ref"]] + (refs if isinstance(refs, list) else [])
        if isinstance(refs, list) and refs:
            for i, r in enumerate(refs[:5], 1):
                if not isinstance(r, dict):
                    continue
                title = r.get("title") or r.get("name") or r.get("id") or f"参考{i}"
                sim = r.get("similarity") or r.get("score") or r.get("sim")
                sim_txt = f"相似度 {sim:.1%}" if isinstance(sim, (int, float)) else ""
                lines.append(f"{i}. {title}　{sim_txt}")
        qf = data.get("query_fingerprint") or {}
        vtype = qf.get("video_type")
        if vtype:
            lines.append(f"<br>判定结构：<b>{vtype}</b>")
        self.bench_text.setHtml("<br>".join(lines) or
                               "<i style='color:#888'>比对结果为空。</i>")

    # -- 日志与用量页（v5.0.0 决策单）--
    def _build_usage_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.usage_text = QTextEdit()
        self.usage_text.setReadOnly(True)
        self.usage_text.setPlaceholderText("运行后此处显示步骤用时 / 模型用时 / API 用量统计…")
        v.addWidget(self.usage_text, 1)
        self.tabs.addTab(page, "日志与用量")

    def refresh_usage(self, stats: str = ""):
        if stats:
            self.usage_text.setHtml(stats)
            return
        work = self.cfg.project_root / "work"
        u = _read_json_safe(work / "ai_usage.jsonl")
        lines = ["<b>步骤用时：</b>见运行日志<br>", "<b>模型用时 / API 用量：</b>占位统计"]
        self.usage_text.setHtml("<br>".join(lines))

    # -- 风格页 --
    def _build_style_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.chk_auto = QCheckBox("自动匹配风格（按语义档案推荐）")
        self.chk_auto.setChecked(bool(self.cfg.get("auto_style", True)))
        self.chk_auto.toggled.connect(self._on_auto_toggle)
        v.addWidget(self.chk_auto)

        self.style_grid_host = QWidget()
        self.style_grid = QGridLayout(self.style_grid_host)
        self.style_grid.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.style_grid_host)
        v.addWidget(scroll, 1)
        self.tabs.addTab(page, "风格")

    def load_styles(self, styles: list[dict]):
        self.styles = styles
        # 清掉旧卡片
        while self.style_grid.count():
            item = self.style_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.style_buttons: list[QPushButton] = []
        cols = 1
        for i, s in enumerate(styles):
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setText(f'{s.get("名称", s.get("id"))}\n'
                        f'适用：{ "、".join(s.get("适用", [])[:2]) }')
            btn.setMinimumHeight(64)
            btn.setStyleSheet("text-align:left; padding:8px;")
            btn.clicked.connect(lambda _=False, sid=s["id"]: self._pick_style(sid))
            self.style_grid.addWidget(btn, i // cols, i % cols)
            self.style_buttons.append(btn)
            if s["id"] == self.cfg.get("last_style_id"):
                btn.setChecked(True)
        self.selected_style = self.cfg.get("last_style_id", "koubo-zhishi")
        self._on_auto_toggle(self.chk_auto.isChecked())

    def _on_auto_toggle(self, on: bool):
        for b in self.style_buttons:
            b.setEnabled(not on)

    def _pick_style(self, sid: str):
        self.selected_style = sid
        for b in self.style_buttons:
            b.setChecked(False)
        # 找回对应按钮
        idx = next((i for i, s in enumerate(self.styles) if s["id"] == sid), 0)
        if 0 <= idx < len(self.style_buttons):
            self.style_buttons[idx].setChecked(True)
        self.changed.emit()

    # -- 配音页 --
    def _build_voice_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel("配音文案（留空则不配音）"))
        self.txt_voiceover = QTextEdit()
        self.txt_voiceover.setPlaceholderText("输入要朗读的旁白文案…")
        v.addWidget(self.txt_voiceover, 1)

        v.addWidget(QLabel("音色"))
        self.cmb_voice = QComboBox()
        for vid, name in VOICES:
            self.cmb_voice.addItem(name, vid)
        idx = next((i for i, (vid, _) in enumerate(VOICES)
                    if vid == self.cfg.get("voice")), 0)
        self.cmb_voice.setCurrentIndex(idx)
        v.addWidget(self.cmb_voice)

        row = QHBoxLayout()
        self.btn_tts = QPushButton("🔊 试听")
        self.btn_tts.clicked.connect(self.preview_tts.emit)
        row.addWidget(self.btn_tts)
        v.addLayout(row)
        self.tabs.addTab(page, "配音")

    # -- 运镜页 --
    def _build_camera_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.chk_keyframes = QCheckBox("启用关键帧运镜")
        self.chk_keyframes.setChecked(True)
        v.addWidget(self.chk_keyframes)
        v.addWidget(QLabel("运镜方式"))
        self.cmb_kf = QComboBox()
        self.cmb_kf.addItem("缓慢放大", "slow-zoom")
        self.cmb_kf.addItem("开头淡入", "fade-in")
        self.cmb_kf.addItem("放大+淡入", "slow-zoom+fade-in")
        self.cmb_kf.addItem("无", "none")
        v.addWidget(self.cmb_kf)
        v.addStretch(1)
        self.tabs.addTab(page, "运镜")

    # -- 画面适配页（v3.7.0 智能画面适配引擎）--
    def _build_visual_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        box = QGroupBox("智能画面适配")
        bv = QVBoxLayout(box)

        self.chk_visual = QCheckBox("启用智能画面适配")
        self.chk_visual.setChecked(bool(self.cfg.get("visual_adapter", True)))
        self.chk_visual.toggled.connect(self._on_visual_toggle)
        bv.addWidget(self.chk_visual)

        bv.addWidget(QLabel("适配密度"))
        self.cmb_density = QComboBox()
        self.cmb_density.addItem("低（仅关键信息）", "low")
        self.cmb_density.addItem("中（默认）", "medium")
        self.cmb_density.addItem("高（密集叠加）", "high")
        default_density = self.cfg.get("visual_density", "medium")
        didx = next((i for i in range(self.cmb_density.count())
                     if self.cmb_density.itemData(i) == default_density), 1)
        self.cmb_density.setCurrentIndex(didx)
        bv.addWidget(self.cmb_density)

        self.btn_visual_lib = QPushButton("管理视觉素材库")
        self.btn_visual_lib.clicked.connect(self._open_visual_lib)
        bv.addWidget(self.btn_visual_lib)

        self.lbl_visual_count = QLabel("…")
        self.lbl_visual_count.setObjectName("muted")
        self.lbl_visual_count.setWordWrap(True)
        bv.addWidget(self.lbl_visual_count)

        v.addWidget(box)
        v.addStretch(1)
        self.tabs.addTab(page, "画面适配")

        self._on_visual_toggle(self.chk_visual.isChecked())
        self.refresh_visual_asset_count()

    def _on_visual_toggle(self, on: bool):
        # 开关关闭时，密度下拉框随之禁用（与现有"自动匹配风格→禁用风格按钮"一致）
        self.cmb_density.setEnabled(on)

    def _open_visual_lib(self):
        """打开 content-library\\visuals\\ 素材库文件夹（不存在则先建空目录）。"""
        d = self.cfg.content_library / "visuals"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def refresh_visual_asset_count(self):
        """从 manifest.json 读取素材计数；文件不存在/损坏时容错提示未初始化。"""
        mf = self.cfg.content_library / "visuals" / "manifest.json"
        count = None
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            for key in ("assets", "items", "visuals", "sprites", "library"):
                if isinstance(data.get(key), list):
                    count = len(data[key])
                    break
        except Exception:
            count = None
        if count is None:
            # 回退：直接数 PNG
            try:
                pngs = list((self.cfg.content_library / "visuals").glob("*.png"))
                count = len(pngs) if pngs else None
            except Exception:
                count = None
        if count:
            self.lbl_visual_count.setText(f"已加载 {count} 个视觉素材")
        else:
            self.lbl_visual_count.setText("素材库未初始化\n（点击「管理视觉素材库」放入 PNG 与 manifest.json）")

    # -- 高级页 --
    def _build_advanced_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.chk_semantic = QCheckBox("语义驱动（高光钩子交叉投票）")
        self.chk_semantic.setChecked(bool(self.cfg.get("semantic", True)))
        v.addWidget(self.chk_semantic)
        self.chk_knowledge = QCheckBox('学习库驱动剪辑（结构、节奏、字幕与画面）')
        self.chk_knowledge.setChecked(bool(self.cfg.get('knowledge', True)))
        self.chk_knowledge.toggled.connect(lambda checked: self.cfg.set('knowledge', checked))
        v.addWidget(self.chk_knowledge)
        self.chk_smart = QCheckBox('智能粗剪（去口误、重复与停顿）')
        v.addWidget(self.chk_smart)
        self.txt_goal = QLineEdit()
        self.txt_goal.setPlaceholderText('制作目标，例如：面向新手，步骤清晰，保留操作细节')
        v.addWidget(self.txt_goal)
        self.chk_publish = QCheckBox("出片后自动发布到剪映草稿目录")
        self.chk_publish.setChecked(bool(self.cfg.get("publish", True)))
        v.addWidget(self.chk_publish)

        v.addWidget(QLabel("封面文案"))
        self.txt_cover = QLineEdit()
        self.txt_cover.setPlaceholderText("留空则自动取首句前 14 字")
        v.addWidget(self.txt_cover)

        v.addWidget(QLabel("行动指令 CTA"))
        self.txt_cta = QLineEdit()
        self.txt_cta.setPlaceholderText("如：评论区扣 1 领取攻略")
        v.addWidget(self.txt_cta)
        v.addStretch(1)
        self.tabs.addTab(page, "高级")

    # -- 取参数 --
    def collect(self) -> dict:
        return {
            "auto_style": self.chk_auto.isChecked(),
            "style_id": getattr(self, "selected_style", "koubo-zhishi"),
            "voiceover": self.txt_voiceover.toPlainText(),
            "voice": self.cmb_voice.currentData(),
            "keyframes": self.cmb_kf.currentData() if self.chk_keyframes.isChecked() else "none",
            "semantic": self.chk_semantic.isChecked(),
            "knowledge": self.chk_knowledge.isChecked(),
            "smart": self.chk_smart.isChecked(),
            "goal": self.txt_goal.text().strip(),
            "publish": self.chk_publish.isChecked(),
            "cover": self.txt_cover.text().strip(),
            "cta": self.txt_cta.text().strip(),
            "visual_adapter": self.chk_visual.isChecked(),
            "visual_density": (self.cmb_density.currentData()
                               if self.chk_visual.isChecked() else None),
        }


# ---------------------------------------------------------------------------
# 底部：时间线 + 日志
# ---------------------------------------------------------------------------
class BottomPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        # 时间线（简化条带）
        tl_box = QGroupBox("时间线")
        tl = QVBoxLayout(tl_box)
        self.tl_label = QLabel("未选择素材 —— 导入并分析后此处显示镜头条带")
        self.tl_label.setObjectName("muted")
        tl.addWidget(self.tl_label)
        self.tl_bar = QFrame()
        self.tl_bar.setFixedHeight(40)
        self.tl_bar.setStyleSheet("background:#2d2d30; border-radius:4px;")
        tl.addWidget(self.tl_bar)

        # AI 标记层（⚡钩子 / 💡信息卡 / ⏰时钟）
        marker_row = QHBoxLayout()
        self.marker_host = QWidget()
        self.marker_lay = QHBoxLayout(self.marker_host)
        self.marker_lay.setContentsMargins(4, 0, 4, 0)
        self.marker_lay.setSpacing(12)
        self.marker_lay.addStretch(1)
        marker_row.addWidget(self.marker_host)
        tl.addLayout(marker_row)
        self.set_markers([])
        lay.addWidget(tl_box, 1)

        # 日志
        log_box = QGroupBox("运行日志")
        lv = QVBoxLayout(log_box)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)
        lv.addWidget(self.log)
        lay.addWidget(log_box, 2)

        self.setMinimumHeight(220)

    def show_shots(self, shots: list[dict]):
        if not shots:
            self.tl_label.setText("镜头档案为空（可能未跑镜头分析）")
            return
        self.tl_label.setText(f"共 {len(shots)} 个镜头段（按片段着色，点击预览区拖动可定位）")

    def set_markers(self, markers: list[dict]):
        """markers: [{icon, time, text}]。空则给 3 个示例占位标记。"""
        # 清空旧标记（保留末尾 stretch）
        while self.marker_lay.count() > 1:
            it = self.marker_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        if not markers:
            markers = [
                {"icon": "⚡", "time": "00:02", "text": "钩子：开头抓注意力（示例）"},
                {"icon": "💡", "time": "00:06", "text": "信息卡：核心知识点（示例）"},
                {"icon": "⏰", "time": "00:11", "text": "时钟：行动指令收尾（示例）"},
            ]
        for m in markers:
            lbl = QLabel(f"{m.get('icon', '⚡')} {m.get('time', '')}")
            lbl.setObjectName("markerDot")
            tip = f"{m.get('icon','⚡')} {m.get('time','')}\n{m.get('text','')}"
            lbl.setToolTip(tip)
            lbl.setCursor(Qt.PointingHandCursor)
            self.marker_lay.addWidget(lbl, 0)

    def append_log(self, msg: str, level: str = "info"):
        color = {
            "err": "#f48771", "warn": "#d7ba00", "ok": "#4ec9b0",
            "cmd": "#1fa2f2", "stage": "#569cd6", "stdout": "#cccccc",
        }.get(level, "#cccccc")
        self.log.append(f'<span style="color:{color}">{msg}</span>')
