"""视频导出全流程汇总报告（Provenance/Run Report）。

在 agent 闭环尾部自动生成一份 Markdown 报告，记录：
  · 素材清单（来源 / 文件数 / 总大小 / 总时长）
  · 全流程阶段记录（感知 / 规划 / 执行 / 评审 / 导出，每阶段含耗时）
  · 操作日志（操作级可追溯：时间戳 | 动作 | 对象 | 结果 | 详情）
  · 所用能力汇总（技能数 / 模型名 / 引擎版本 / 剪映版本）

设计原则：
  · 如实记录——没有实际执行的操作记"待用户操作"或"未执行"，不虚构
  · ffprobe 仅分析用途（探测素材元信息），不做转码
  · 不记录敏感信息（API key 等）
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import __version__ as ENGINE_VERSION
from .paths import DATA, REPORTS, ffprobe

# 模型名（与 llm.py 一致）
MODEL_NAME = "Ollama 本地多模态（qwen3-vl:8b / gemma4:12b / qwen3.5:9b）"


def _read_jianying_version() -> str:
    """从 data/jy_version_state.json 读剪映版本号；文件不存在则记"未检测到"。"""
    path = DATA / "jy_version_state.json"
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return str(data.get("version") or "未知")
    except Exception:  # noqa: BLE001
        pass
    return "未检测到"


def _probe_duration(video_path: str | Path) -> float | None:
    """用 ffprobe 探测素材时长（秒），仅分析用途。失败返回 None。"""
    video_path = Path(video_path)
    if not video_path.exists():
        return None
    try:
        cmd = [ffprobe(), "-v", "quiet", "-print_format", "json",
               "-show_format", "-show_streams", str(video_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "{}")
        fmt = data.get("format") or {}
        dur = fmt.get("duration")
        if dur is not None:
            return round(float(dur), 2)
    except Exception:  # noqa: BLE001
        pass
    return None


def _human_size(num_bytes: int) -> str:
    """字节 -> 可读大小。"""
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.2f} MB"


class ExportReport:
    """全流程导出报告：逐步记录 + 一键生成 Markdown。

    用法一（逐步记录）：
        rep = ExportReport("我的草稿")
        rep.stage_start("感知")
        ... 做感知 ...
        rep.stage_end("感知")
        rep.set_analysis_result(shot_count=12, scene_count=3, vlm_calls=8)
        rep.log_operation("创建草稿", "draft_content.json", "成功")
        rep.save()

    用法二（上下文管理器）：
        with ExportReport("我的草稿") as rep:
            rep.stage_start("感知")
            ...
            rep.stage_end("感知")
            # 退出 with 时自动 save()
    """

    def __init__(self, draft_name: str, output_dir: str | Path = str(REPORTS)) -> None:
        self.draft_name = draft_name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 元信息
        self.started_at = time.time()
        self.generated_at_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.engine_version = ENGINE_VERSION
        self.jianying_version = _read_jianying_version()
        self.model_name = MODEL_NAME
        self.dry_run = False

        # 阶段记录
        self._stages: dict[str, dict] = {}
        # 操作日志
        self._operations: list[dict] = []
        # 素材清单
        self._materials: list[dict] = []
        # 各阶段结果
        self._analysis: dict = {}
        self._plan: dict = {}
        self._execution: dict = {}
        self._review: dict = {}
        self._export: dict = {}
        self._open_draft: dict = {}
        self._quality: dict = {}

    # ---------- 上下文管理器 ----------

    def __enter__(self) -> "ExportReport":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.save()

    # ---------- 阶段计时 ----------

    def stage_start(self, stage_name: str) -> None:
        self._stages[stage_name] = {"start": time.time(), "end": None, "duration": None}

    def stage_end(self, stage_name: str) -> None:
        info = self._stages.get(stage_name)
        if info is None:
            self._stages[stage_name] = {"start": time.time(), "end": time.time(),
                                         "duration": 0.0}
            return
        info["end"] = time.time()
        info["duration"] = round(info["end"] - info["start"], 2)

    # ---------- 操作日志 ----------

    def log_operation(self, action: str, target: str, result: str,
                     details: str | None = None) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._operations.append({
            "timestamp": ts,
            "action": action,
            "target": str(target) if target else "-",
            "result": str(result) if result else "-",
            "details": str(details) if details else "",
        })

    # ---------- 各阶段数据设置 ----------

    def set_materials(self, material_list: list[dict]) -> None:
        """设置素材清单。每项：{path, name, size_bytes, duration}。

        如果 duration 为 None，内部会用 ffprobe 探测。
        """
        self._materials = []
        for item in material_list:
            path = item.get("path", "")
            name = item.get("name") or (Path(path).name if path else "未知")
            size_bytes = item.get("size_bytes")
            if size_bytes is None and path:
                try:
                    size_bytes = os.path.getsize(path)
                except OSError:
                    size_bytes = 0
            duration = item.get("duration")
            if duration is None and path:
                duration = _probe_duration(path)
            self._materials.append({
                "path": str(path),
                "name": name,
                "size_bytes": size_bytes or 0,
                "size_human": _human_size(size_bytes or 0),
                "duration": duration,
            })

    def set_analysis_result(self, shot_count: int, scene_count: int,
                            vlm_calls: int) -> None:
        self._analysis = {
            "shot_count": shot_count,
            "scene_count": scene_count,
            "vlm_calls": vlm_calls,
        }

    def set_plan_result(self, decision: str, style_card: str, steps: int) -> None:
        self._plan = {
            "decision": str(decision or ""),
            "style_card": str(style_card or ""),
            "steps": steps,
        }

    def set_execution_result(self, draft_path: str | None, skills_called: list[str],
                             engine_apis: list[str] | None = None,
                             resources_used: dict | None = None) -> None:
        self._execution = {
            "draft_path": str(draft_path) if draft_path else "未生成",
            "skills_called": list(skills_called or []),
            "engine_apis": list(engine_apis or []),
            "resources_used": resources_used or {},
        }

    def set_review_result(self, iterations: int, scores_per_round: list[float],
                          final_verdict: str, ready_to_ship: bool) -> None:
        self._review = {
            "iterations": iterations,
            "scores_per_round": list(scores_per_round or []),
            "final_verdict": str(final_verdict or ""),
            "ready_to_ship": bool(ready_to_ship),
        }

    def set_export_result(self, export_method: str, resolution: str, fps: int,
                          duration: float, export_time: float,
                          output_path: str | None) -> None:
        self._export = {
            "export_method": str(export_method or "待用户操作"),
            "resolution": str(resolution or "-"),
            "fps": fps,
            "duration": duration,
            "export_time": round(export_time, 2) if export_time else 0.0,
            "output_path": str(output_path) if output_path else "未导出",
        }

    def set_open_draft_result(self, method: str, timestamp: str, result: str) -> None:
        self._open_draft = {
            "method": str(method or "待用户操作"),
            "timestamp": str(timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "result": str(result or ""),
        }

    def set_quality_result(self, clean: dict | None = None,
                           compare: dict | None = None) -> None:
        """设置画面质量验证结果（cleancheck 干净度 + before/after 比对）。

        Args:
            clean: cleancheck.check_frame 返回的干净度结果
            compare: compare.compare_frames 返回的比对结果
        """
        self._quality = {
            "clean": dict(clean or {}),
            "compare": dict(compare or {}),
        }

    # ---------- 生成报告 ----------

    def _total_material_size_mb(self) -> float:
        return round(sum(m["size_bytes"] for m in self._materials) / (1024 * 1024), 2)

    def _total_material_duration(self) -> float:
        return round(sum(m["duration"] or 0 for m in self._materials), 2)

    def generate(self, fmt: str = "md") -> str:
        """生成报告文本。当前支持 Markdown。"""
        if fmt != "md":
            # HTML 暂不实现，降级为 Markdown
            pass

        lines: list[str] = []

        # ---- 标题与元信息 ----
        lines.append("# 视频导出全流程汇总报告")
        lines.append("")
        lines.append(f"- **草稿名**：{self.draft_name}")
        lines.append(f"- **报告生成时间**：{self.generated_at_str}")
        lines.append(f"- **引擎版本号**：{self.engine_version}")
        lines.append(f"- **剪映版本号**：{self.jianying_version}")
        lines.append(f"- **模型**：{self.model_name}")
        if self.dry_run:
            lines.append(f"- **模式**：⚠️ 演算模式（未实际修改草稿）")
        lines.append("")

        # ---- 素材清单 ----
        lines.append("## 一、素材清单")
        lines.append("")
        if self._materials:
            lines.append("| # | 文件名 | 来源路径 | 大小 | 时长(秒) |")
            lines.append("|---|--------|---------|------|---------|")
            for i, m in enumerate(self._materials, 1):
                dur = f"{m['duration']:.2f}" if m["duration"] is not None else "未知"
                lines.append(f"| {i} | {m['name']} | {m['path']} | {m['size_human']} | {dur} |")
            total_size = self._total_material_size_mb()
            total_dur = self._total_material_duration()
            lines.append(f"| **合计** | **{len(self._materials)} 个文件** | - | "
                         f"**{total_size:.2f} MB** | **{total_dur:.2f}** |")
        else:
            lines.append("无素材记录。")
        lines.append("")

        # ---- 全流程阶段记录 ----
        lines.append("## 二、全流程阶段记录")
        lines.append("")

        # a) 感知/分析
        a = self._analysis
        stage_dur = self._stage_duration("感知")
        lines.append(f"### a) 感知 / 分析"
                     + (f"（耗时 {stage_dur:.2f}s）" if stage_dur is not None else ""))
        lines.append("")
        if a:
            lines.append(f"- 镜头数：{a.get('shot_count', '?')}")
            lines.append(f"- 场景数：{a.get('scene_count', '?')}")
            lines.append(f"- VLM 调用次数：{a.get('vlm_calls', '?')}")
        else:
            lines.append("- 未执行分析")
        lines.append("")

        # b) 规划
        p = self._plan
        stage_dur = self._stage_duration("规划")
        lines.append(f"### b) 规划"
                     + (f"（耗时 {stage_dur:.2f}s）" if stage_dur is not None else ""))
        lines.append("")
        if p:
            lines.append(f"- 决策摘要：{p.get('decision', '?')}")
            lines.append(f"- 风格卡：{p.get('style_card', '?')}")
            lines.append(f"- 步骤数：{p.get('steps', '?')}")
        else:
            lines.append("- 未执行规划")
        lines.append("")

        # c) 执行
        e = self._execution
        stage_dur = self._stage_duration("执行")
        lines.append(f"### c) 执行"
                     + (f"（耗时 {stage_dur:.2f}s）" if stage_dur is not None else ""))
        lines.append("")
        if e:
            lines.append(f"- 剪映草稿路径：{e.get('draft_path', '未生成')}")
            skills = e.get("skills_called") or []
            lines.append(f"- 调用技能清单（{len(skills)} 个）：")
            for sname in skills:
                lines.append(f"  - {sname}")
            apis = e.get("engine_apis") or []
            if apis:
                lines.append(f"- 草稿引擎 API 调用：{'、'.join(apis)}")
            res = e.get("resources_used") or {}
            if res:
                lines.append(f"- 资源使用：{json.dumps(res, ensure_ascii=False)}")
        else:
            lines.append("- 未执行")
        lines.append("")

        # d) 评审
        r = self._review
        stage_dur = self._stage_duration("评审")
        lines.append(f"### d) 评审"
                     + (f"（耗时 {stage_dur:.2f}s）" if stage_dur is not None else ""))
        lines.append("")
        if r:
            lines.append(f"- 迭代轮数：{r.get('iterations', '?')}")
            scores = r.get("scores_per_round") or []
            if scores:
                score_str = " → ".join(f"{s:.1f}" for s in scores)
                lines.append(f"- 每轮得分：{score_str}")
            lines.append(f"- 最终判定：{r.get('final_verdict', '?')}")
            lines.append(f"- ready_to_ship：{'✅ 是' if r.get('ready_to_ship') else '❌ 否'}")
        else:
            lines.append("- 未执行评审（dry_run 模式）")
        lines.append("")

        # e) 导出
        x = self._export
        stage_dur = self._stage_duration("导出")
        lines.append(f"### e) 导出"
                     + (f"（耗时 {stage_dur:.2f}s）" if stage_dur is not None else ""))
        lines.append("")
        if x:
            lines.append(f"- 导出触发方式：{x.get('export_method', '待用户操作')}")
            lines.append(f"- 分辨率：{x.get('resolution', '-')}")
            lines.append(f"- 帧率：{x.get('fps', '-')} fps")
            lines.append(f"- 成片时长：{x.get('duration', '-')}s")
            lines.append(f"- 导出耗时：{x.get('export_time', 0):.2f}s")
            lines.append(f"- 导出文件路径：{x.get('output_path', '未导出')}")
        else:
            lines.append("- 未执行导出（待用户操作）")
            lines.append("  - 请在剪映中打开草稿，然后导出。")
        # 打开草稿记录
        od = self._open_draft
        if od:
            lines.append(f"- 打开草稿方式：{od.get('method', '待用户操作')}")
            lines.append(f"- 时间：{od.get('timestamp', '-')}")
            lines.append(f"- 结果：{od.get('result', '')}")
        else:
            lines.append("- 打开草稿：待用户操作（双击草稿路径）")
            if e and e.get("draft_path") and e["draft_path"] != "未生成":
                lines.append(f"  - 草稿路径：{e['draft_path']}")
        lines.append("")

        # ---- 操作日志 ----
        lines.append("## 三、操作日志（操作级可追溯）")
        lines.append("")
        lines.append("| 时间戳 | 动作 | 对象 | 结果 | 详情 |")
        lines.append("|--------|------|------|------|------|")
        for op in self._operations:
            details = op["details"].replace("|", "/").replace("\n", " ") if op["details"] else ""
            lines.append(f"| {op['timestamp']} | {op['action']} | {op['target']} | "
                         f"{op['result']} | {details} |")
        if not self._operations:
            lines.append("| - | （无操作记录） | - | - | - |")
        lines.append("")

        # ---- 所用能力汇总 ----
        lines.append("## 四、所用能力汇总")
        lines.append("")
        skills_used = (self._execution.get("skills_called") or []) if self._execution else []
        lines.append(f"- 本次运行技能数：{len(skills_used)}")
        if skills_used:
            lines.append(f"- 技能列表：{'、'.join(skills_used)}")
        mcp_count = 0  # 本项目不使用 MCP 工具
        lines.append(f"- MCP 工具数：{mcp_count}")
        lines.append(f"- 模型名：{self.model_name}")
        lines.append(f"- 引擎版本号：{self.engine_version}")
        lines.append(f"- 剪映版本号：{self.jianying_version}")
        lines.append("")

        # ---- 画面质量验证（cleancheck + before/after 比对） ----
        lines.append("## 五、画面质量验证")
        lines.append("")
        q = self._quality
        clean = q.get("clean") or {}
        cmp = q.get("compare") or {}
        if clean:
            score = clean.get("clean_score")
            level = clean.get("level", "?")
            dims = clean.get("dims") or {}
            lines.append(f"- **干净度评分**：{score} / 100（{level}）")
            if dims:
                dim_str = "、".join(f"{k}={v}" for k, v in dims.items())
                lines.append(f"- 五维得分：{dim_str}")
            issues = clean.get("issues") or []
            if issues:
                lines.append(f"- 检测问题（{len(issues)}）：")
                for iss in issues:
                    lines.append(f"  - ⚠ {iss}")
            advice = clean.get("advice") or []
            if advice:
                lines.append("- 处理建议：")
                for ad in advice:
                    lines.append(f"  - {ad}")
        else:
            lines.append("- 未执行画面干净检查（cleancheck）")
        lines.append("")
        if cmp:
            bm = cmp.get("before_metrics") or {}
            am = cmp.get("after_metrics") or {}
            lines.append(f"- **before/after 比对**：")
            if bm:
                lines.append(f"  - 原素材帧：干净度 {bm.get('clean_score', '?')}（{bm.get('clean_level', '?')}）"
                             f"· 亮度 {bm.get('brightness', '?')} · 清晰度 {bm.get('sharpness', '?')}")
            if am:
                lines.append(f"  - 成片帧：干净度 {am.get('clean_score', '?')}（{am.get('clean_level', '?')}）"
                             f"· 亮度 {am.get('brightness', '?')} · 清晰度 {am.get('sharpness', '?')}")
            diff_n = cmp.get("diff_count", 0)
            lines.append(f"  - 显著差异区：{diff_n} 处")
            out = cmp.get("output")
            if out:
                lines.append(f"  - 比对图：{out}")
        else:
            lines.append("- 未执行 before/after 比对")
        lines.append("")

        # ---- 页脚 ----
        lines.append("---")
        lines.append(f"*报告由 ExportReport 自动生成 · {self.generated_at_str}*")

        return "\n".join(lines)

    def _stage_duration(self, name: str) -> float | None:
        info = self._stages.get(name)
        if info and info.get("duration") is not None:
            return info["duration"]
        return None

    # ---------- 保存 ----------

    def save(self) -> str:
        """保存报告到文件，返回文件路径。"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        # 草稿名做文件名安全处理
        safe_name = "".join(c for c in self.draft_name if c.isalnum() or c in "-_") or "draft"
        filename = f"export_report_{date_str}_{safe_name}.md"
        filepath = self.output_dir / filename
        content = self.generate("md")
        filepath.write_text(content, encoding="utf-8")
        return str(filepath)
