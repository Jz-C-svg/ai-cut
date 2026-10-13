"""模板化剪辑对话框（v3.8.0）。

五步流程：选素材 → 分析指纹 → 联网找参考 → 提炼模板 → 套用模板出片。

设计要点：
  · 不重写现有面板，独立 QDialog，从 MainWindow 取当前选中素材；
  · 所有重型操作（指纹/找参考/出片）通过 MainWindow.run_template_task 走
    PipelineWorker（template_analyze / template_benchmark / template_edit），不阻塞 UI；
  · 日志统一回传到底部 BottomPanel；
  · 模板列表直接读 content-library/templates/*.json（GUI 线程纯文件操作，毫秒级）；
  · 从剪映草稿导入素材走 gui.realtime_bridge（P1 简化版）。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QComboBox, QGroupBox, QMessageBox,
    QLineEdit, QDialogButtonBox,
)

from . import realtime_bridge as rb


def _read_json(p: str | Path) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


class TemplateClipDialog(QDialog):
    """模板化剪辑主对话框。"""

    def __init__(self, mainwin, parent=None):
        super().__init__(parent or mainwin)
        self.mw = mainwin
        self.cfg = mainwin.cfg
        self.setWindowTitle("🧠 模板化剪辑（v3.8.0）")
        self.resize(760, 720)
        self._benchmark_result: dict | None = None
        self._fingerprint: dict | None = None
        self._build_ui()
        self.refresh_templates()
        self._sync_material()

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        root = QVBoxLayout(self)

        # 1) 当前素材
        g_mat = QGroupBox("① 当前素材")
        v1 = QVBoxLayout(g_mat)
        self.lbl_material = QLabel("未选择素材")
        self.lbl_material.setObjectName("muted")
        v1.addWidget(self.lbl_material)
        row_mat = QHBoxLayout()
        self.btn_refresh_mat = QPushButton("🔄 刷新当前素材")
        self.btn_refresh_mat.clicked.connect(self._sync_material)
        self.btn_import_draft = QPushButton("📽 从剪映草稿导入")
        self.btn_import_draft.clicked.connect(self._import_from_draft)
        row_mat.addWidget(self.btn_refresh_mat)
        row_mat.addWidget(self.btn_import_draft)
        row_mat.addStretch(1)
        v1.addLayout(row_mat)
        root.addWidget(g_mat)

        # 2) 素材指纹
        g_fp = QGroupBox("② 素材指纹（剪辑结构特征）")
        v2 = QVBoxLayout(g_fp)
        self.lbl_fp = QLabel("尚未分析。点击「分析素材指纹」提取类型/节奏/钩子。")
        self.lbl_fp.setWordWrap(True)
        self.lbl_fp.setObjectName("muted")
        v2.addWidget(self.lbl_fp)
        self.btn_fingerprint = QPushButton("🔍 分析素材指纹")
        self.btn_fingerprint.clicked.connect(self._on_fingerprint)
        v2.addWidget(self.btn_fingerprint)
        root.addWidget(g_fp)

        # 3) 联网找参考
        g_bench = QGroupBox("③ 联网找参考（Top 参考 + 分维度打分）")
        v3 = QVBoxLayout(g_bench)
        self.btn_benchmark = QPushButton("🌐 联网找参考")
        self.btn_benchmark.clicked.connect(self._on_benchmark)
        v3.addWidget(self.btn_benchmark)
        self.list_cands = QListWidget()
        self.list_cands.setMinimumHeight(140)
        self.list_cands.currentItemChanged.connect(self._show_candidate_detail)
        v3.addWidget(self.list_cands)
        self.lbl_cand_detail = QLabel("点击上方某条参考，这里展开分维度得分与匹配理由。")
        self.lbl_cand_detail.setWordWrap(True)
        self.lbl_cand_detail.setObjectName("muted")
        v3.addWidget(self.lbl_cand_detail)
        self.btn_gen_template = QPushButton("📋 从选中参考生成模板")
        self.btn_gen_template.clicked.connect(self._on_generate_template)
        v3.addWidget(self.btn_gen_template)
        root.addWidget(g_bench)

        # 4) 模板库
        g_lib = QGroupBox("④ 模板库（直接套用已有模板）")
        v4 = QVBoxLayout(g_lib)
        self.cmb_templates = QComboBox()
        v4.addWidget(self.cmb_templates)
        row_lib = QHBoxLayout()
        self.btn_refresh_lib = QPushButton("🔄 刷新")
        self.btn_refresh_lib.clicked.connect(self.refresh_templates)
        self.btn_delete_tmpl = QPushButton("🗑 删除")
        self.btn_delete_tmpl.clicked.connect(self._on_delete_template)
        row_lib.addWidget(self.btn_refresh_lib)
        row_lib.addWidget(self.btn_delete_tmpl)
        row_lib.addStretch(1)
        v4.addLayout(row_lib)
        self.lbl_tmpl_summary = QLabel("模板参数摘要（结构/节奏/技术）")
        self.lbl_tmpl_summary.setWordWrap(True)
        self.lbl_tmpl_summary.setObjectName("muted")
        v4.addWidget(self.lbl_tmpl_summary)
        self.cmb_templates.currentIndexChanged.connect(self._show_template_summary)
        root.addWidget(g_lib)

        # 5) 出片
        g_edit = QGroupBox("⑤ 套用模板出片")
        v5 = QVBoxLayout(g_edit)
        row_name = QHBoxLayout()
        row_name.addWidget(QLabel("成片名"))
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("留空自动命名")
        row_name.addWidget(self.edit_name)
        v5.addLayout(row_name)
        self.btn_edit = QPushButton("🎬 套用模板出片（auto_edit --template）")
        self.btn_edit.clicked.connect(self._on_template_edit)
        v5.addWidget(self.btn_edit)
        root.addWidget(g_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ---------------------------------------------------------------- 素材
    def _current_video(self) -> str | None:
        it = self.mw.material.current_item()
        if not it:
            return None
        v = it.get("video") or it.get("source")
        return v if v and Path(v).exists() else None

    def _sync_material(self):
        v = self._current_video()
        if v:
            it = self.mw.material.current_item() or {}
            self.lbl_material.setText(
                f"当前素材：{Path(v).name}\n来源：{it.get('source', v)}")
            self.edit_name.setText(f"{Path(v).stem}-templated")
        else:
            self.lbl_material.setText(
                "⚠ 左侧素材库未选中素材。请先导入素材并选中，或点「从剪映草稿导入」。")

    # ---------------------------------------------------------------- 草稿导入
    def _import_from_draft(self):
        drafts = rb.list_jianying_drafts(self.cfg.get("draft_root"))
        if not drafts:
            QMessageBox.information(
                self, "没有草稿",
                "未在剪映草稿目录找到草稿。\n目录：" + str(self.cfg.get("draft_root")))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("选择剪映草稿")
        dlg.resize(520, 360)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("选择最近的草稿，提取其中的素材文件："))
        lw = QListWidget()
        for d in drafts:
            QListWidgetItem(f"{d['mtime_str']}   {d['name']}", lw)
        lay.addWidget(lw, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() and lw.currentRow() >= 0:
            chosen = drafts[lw.currentRow()]
            mats = rb.extract_materials_from_draft(chosen["path"])
            if not mats:
                QMessageBox.information(self, "未提取到素材",
                                        "该草稿未找到本地视频文件（可能素材已移动）。")
                return
            self.mw.bottom.append_log(
                f"从草稿「{chosen['name']}」提取到 {len(mats)} 个素材", "ok")
            self.mw.material.load_materials(mats)
            if mats:
                self.mw.preview.load_video(mats[0])
            self._sync_material()
            QMessageBox.information(self, "导入完成",
                                    f"已从草稿「{chosen['name']}」导入 {len(mats)} 个素材。")

    # ---------------------------------------------------------------- 指纹
    def _on_fingerprint(self):
        v = self._current_video()
        if not v:
            QMessageBox.warning(self, "提示", "请先在左侧选择一个素材")
            return
        out_json = str(self.cfg.project_root / "work" /
                       f"fingerprint-gui-{Path(v).stem}.json")
        self._set_busy(True)
        self.mw.bottom.append_log("—— 开始分析素材指纹 ——", "stage")
        self.mw.run_template_task(
            "template_analyze", {"video": v, "out_json": out_json},
            self._on_fingerprint_done)

    def _on_fingerprint_done(self, ok: bool, result: str):
        self._set_busy(False)
        if not ok:
            self.lbl_fp.setText(f"✘ 指纹分析失败：{result}")
            return
        try:
            self._fingerprint = _read_json(result)
        except Exception as e:  # noqa: BLE001
            self.lbl_fp.setText(f"✘ 读取指纹失败：{e}")
            return
        fp = self._fingerprint
        r = fp.get("rhythm", {}) or {}
        s = fp.get("structure", {}) or {}
        a = fp.get("audio", {}) or {}
        self.lbl_fp.setText(
            f"类型：{fp.get('video_type','?')}    时长：{fp.get('duration',0)}s\n"
            f"钩子：{s.get('hook_type','?')}（{(s.get('hook_text') or '')[:20]}）\n"
            f"主体节奏：{s.get('body_rhythm','?')}    结尾：{s.get('ending_type','?')}\n"
            f"镜头：{r.get('shot_count',0)} 个，均镜 {r.get('avg_shot',0)}s，"
            f"{r.get('pace','?')}    人声比 {a.get('speech_ratio',0)}")

    # ---------------------------------------------------------------- 找参考
    def _on_benchmark(self):
        v = self._current_video()
        if not v:
            QMessageBox.warning(self, "提示", "请先在左侧选择一个素材")
            return
        out_json = str(self.cfg.project_root / "work" /
                       f"benchmark-gui-{Path(v).stem}.json")
        self._set_busy(True)
        self.mw.bottom.append_log("—— 开始联网找参考（含降级）——", "stage")
        self.mw.run_template_task(
            "template_benchmark",
            {"video": v, "out_json": out_json, "max": 3},
            self._on_benchmark_done)

    def _on_benchmark_done(self, ok: bool, result: str):
        self._set_busy(False)
        if not ok:
            QMessageBox.warning(self, "找参考失败", result)
            return
        try:
            self._benchmark_result = _read_json(result)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "读取结果失败", str(e))
            return
        self.list_cands.clear()
        cands = self._benchmark_result.get("candidates", [])
        degraded = self._benchmark_result.get("degraded", False)
        if degraded:
            self.mw.bottom.append_log(
                f"降级模式：{self._benchmark_result.get('degraded_reason','')}，"
                "已用本地素材做模拟参考", "warn")
        for c in cands:
            comp = c.get("comparison") or {}
            src = "本地模拟" if c.get("source", "").startswith("local") else c.get("source", "")
            item = QListWidgetItem(
                f"Top{c.get('rank')}  总分 {comp.get('total_score',0):.3f}  "
                f"[{src}]  {Path(c.get('title','')).name}")
            item.setData(Qt.UserRole, c)
            self.list_cands.addItem(item)
        if self.list_cands.count():
            self.list_cands.setCurrentRow(0)
        self.mw.bottom.append_log(
            f"找参考完成：{len(cands)} 个候选"
            + ("（降级模式）" if degraded else ""), "ok")

    def _show_candidate_detail(self, cur, _prev=None):
        if not cur:
            return
        c = cur.data(Qt.UserRole) or {}
        comp = c.get("comparison") or {}
        dims = comp.get("dimensions", {}) or {}
        lines = [f"总分：{comp.get('total_score',0):.3f}",
                 f"匹配理由：{comp.get('match_reason','')}"]
        for k, label in (("type", "类型"), ("structure", "结构"),
                         ("rhythm", "节奏"), ("content", "内容"),
                         ("shot_pattern", "镜头模式")):
            d = dims.get(k, {}) or {}
            lines.append(f"  · {label}: {d.get('score',0):.2f}（权重{d.get('weight',0)}）"
                         f"  {d.get('detail','')}")
        for diff in comp.get("differences", []) or []:
            lines.append(f"  ⚠ {diff}")
        self.lbl_cand_detail.setText("\n".join(lines))

    # ---------------------------------------------------------------- 生成模板
    def _on_generate_template(self):
        cur = self.list_cands.currentItem()
        if not cur:
            QMessageBox.warning(self, "提示", "请先在上方选一条参考（Top1/Top2…）")
            return
        c = cur.data(Qt.UserRole) or {}
        tmpl = c.get("template")
        if not tmpl:
            QMessageBox.warning(self, "提示", "该参考没有可提炼的模板（结果异常）")
            return
        try:
            tdir = self.cfg.content_library / "templates"
            tdir.mkdir(parents=True, exist_ok=True)
            tpath = tdir / f"{tmpl.get('template_id','template')}.json"
            tpath.write_text(json.dumps(tmpl, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "保存模板失败", str(e))
            return
        self.mw.bottom.append_log(f"已生成模板：{tpath.name}", "ok")
        self.refresh_templates()
        # 切到刚生成的模板
        tid = tmpl.get("template_id", "")
        for i in range(self.cmb_templates.count()):
            if self.cmb_templates.itemData(i) == tid:
                self.cmb_templates.setCurrentIndex(i)
                break
        QMessageBox.information(self, "模板已生成",
                                f"模板「{tmpl.get('template_name','')}」已存入模板库。\n"
                                "可在下方模板库选中后点「套用模板出片」。")

    # ---------------------------------------------------------------- 模板库
    def refresh_templates(self):
        self.cmb_templates.blockSignals(True)
        self.cmb_templates.clear()
        tdir = self.cfg.content_library / "templates"
        self._tmpl_paths: dict[str, Path] = {}
        if tdir.exists():
            for p in sorted(tdir.glob("*.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                tid = d.get("template_id", p.stem)
                name = d.get("template_name", tid)
                self.cmb_templates.addItem(f"{name}  ({tid})", tid)
                self._tmpl_paths[tid] = p
        self.cmb_templates.blockSignals(False)
        if self.cmb_templates.count():
            self._show_template_summary(0)

    def _current_template_id(self) -> str | None:
        return self.cmb_templates.currentData()

    def _show_template_summary(self, _idx=None):
        tid = self._current_template_id()
        if not tid or tid not in getattr(self, "_tmpl_paths", {}):
            self.lbl_tmpl_summary.setText("（模板库为空）")
            return
        try:
            d = json.loads(self._tmpl_paths[tid].read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        s = d.get("structure", {}) or {}
        r = d.get("rhythm", {}) or {}
        t = d.get("techniques", {}) or {}
        self.lbl_tmpl_summary.setText(
            f"{d.get('description','')}\n\n"
            f"结构：{s.get('hook_type','?')}开头 → {s.get('body_rhythm','?')} → "
            f"{s.get('ending_type','?')}（{s.get('section_count','?')}段）\n"
            f"节奏：均镜 {r.get('target_avg_shot','?')}s "
            f"[{r.get('min_shot','?')}-{r.get('max_shot','?')}s]，{r.get('pace','?')}\n"
            f"技术：{t.get('transition','?')} / 关键帧{t.get('keyframe_mode','?')} / "
            f"字幕≤{t.get('subtitle_max_chars','?')}字")

    def _on_delete_template(self):
        tid = self._current_template_id()
        if not tid:
            return
        p = self._tmpl_paths.get(tid)
        ret = QMessageBox.question(
            self, "删除模板",
            f"确定删除模板「{tid}」吗？\n文件：{p}",
            QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes and p and p.exists():
            try:
                p.unlink()
                self.mw.bottom.append_log(f"已删除模板 {tid}", "warn")
                self.refresh_templates()
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "删除失败", str(e))

    # ---------------------------------------------------------------- 出片
    def _on_template_edit(self):
        v = self._current_video()
        if not v:
            QMessageBox.warning(self, "提示", "请先在左侧选择一个素材")
            return
        tid = self._current_template_id()
        if not tid:
            QMessageBox.warning(self, "提示", "请先在模板库选择一个模板"
                                  "（可先「分析指纹→找参考→生成模板」）")
            return
        name = self.edit_name.text().strip() or None
        self._set_busy(True)
        self.mw.bottom.append_log(
            f"—— 套用模板 {tid} 出片 ——", "stage")
        self.mw.run_template_task(
            "template_edit",
            {"video": v, "template": tid, "name": name, "publish": True},
            self._on_edit_done)

    def _on_edit_done(self, ok: bool, result: str):
        self._set_busy(False)
        if ok:
            QMessageBox.information(
                self, "出片完成",
                f"模板化成片草稿已生成：\n{result}\n\n可点工具栏「打开剪映」查看。")
        else:
            QMessageBox.warning(self, "出片失败", result)

    # ---------------------------------------------------------------- busy
    def _set_busy(self, busy: bool):
        for b in (self.btn_fingerprint, self.btn_benchmark,
                  self.btn_gen_template, self.btn_edit, self.btn_import_draft):
            b.setEnabled(not busy)
