"""MCP Server：把 aicut 的技能注册表原样暴露成标准 MCP 工具。

设计原则（和 agent.py 调的是同一份代码，零双轨维护）：
  - 协议：JSON-RPC 2.0 over stdio，换行分隔（MCP stdio 传输就是 NDJSON）。
    不依赖第三方 mcp 库，Windows 子进程里最稳、最可控。
  - 工具来源：启动时遍历 skills.REGISTRY，每个 Skill 自动注册一个 MCP 工具。
    工具名 = skill.id；描述 = skill.goal；inputSchema 由 args/required 自动生成。
  - 额外提供会话管理、素材理解与草稿审计工具。
  - 调用路径：构造 skills.Context（draft/video/transcript/dry_run/log）
    → 写操作先 snapshot → skill.run(ctx, args) → 草稿改完 save() → 返回 report JSON。

stdout 只允许出现 JSON-RPC 消息；所有日志/调试一律写 stderr。
"""
from __future__ import annotations

import json
from contextlib import redirect_stdout
import sys
import traceback
from pathlib import Path

from . import skills
from .draft import Draft
from .paths import PROJECT_DRAFTS

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "aicut-mcp"
SERVER_VERSION = "1.1.0"

# 写操作通道：会改动草稿内容，调用前先快照、调用后回写
_WRITE_CHANNELS = {"draft"}
# 不加载草稿也能用的纯查询技能（查特效库等）
_NO_DRAFT_NEEDED = {"list_effects"}


def _log(msg: str) -> None:
    print(f"[aicut-mcp] {msg}", file=sys.stderr, flush=True)


def list_draft_names() -> list[str]:
    """06-drafts/ 下所有含 draft_content.json 的草稿目录。"""
    if not PROJECT_DRAFTS.exists():
        return []
    names = []
    for p in sorted(PROJECT_DRAFTS.iterdir()):
        if p.is_dir() and (p / "draft_content.json").exists():
            names.append(p.name)
    return names


def _infer_video(draft: Draft | None) -> Path | None:
    if draft and draft.video_segments:
        p = Path(draft.video_segments[0].material_path())
        return p if p.exists() else None
    return None


def _resolve_video_path(vp: str, root: Path) -> Path | None:
    """把调用方给的视频路径解析成存在的绝对路径（支持相对 root / work / 绝对）。"""
    if not vp:
        return None
    p = Path(vp)
    if p.exists():
        return p
    for base in (root, root / "work", root / "work" / "normalized"):
        cand = base / vp
        if cand.exists():
            return cand
    return None


def _frac(rate: str) -> float:
    """把 ffprobe 的 '30000/1001' 帧率串解析成 float。"""
    try:
        num, _, den = str(rate).partition("/")
        return round(float(num) / float(den), 3) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


class _State:
    """多草稿会话：记住当前活跃草稿，工具没传 draft_name 就用它。"""

    def __init__(self, production_approvals=None) -> None:
        self.active_draft: str | None = None
        self.production_approvals = dict(production_approvals or {})

    # ---------- 工具：会话管理 ----------

    def tool_list_drafts(self, args: dict) -> dict:
        drafts = list_draft_names()
        return {
            "ok": True,
            "active_draft": self.active_draft,
            "drafts": drafts,
            "count": len(drafts),
        }

    def tool_set_active_draft(self, args: dict) -> dict:
        name = (args.get("draft_name") or "").strip()
        if not name:
            return {"ok": False, "error": "缺少参数 draft_name"}
        target = PROJECT_DRAFTS / name
        if not (target / "draft_content.json").exists():
            return {"ok": False, "error": f"草稿不存在：{name}",
                    "available": list_draft_names()}
        self.active_draft = name
        return {"ok": True, "active_draft": name}

    def tool_draft_status(self, args: dict) -> dict:
        name = (args.get("draft_name") or self.active_draft or "").strip()
        if not name:
            return {"ok": False, "error": "未指定草稿，也没有活跃草稿（先 list_drafts / set_active_draft）"}
        try:
            draft = Draft.from_name(name)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"加载草稿失败：{exc}"}
        tracks = {t.type: len(t.segments) for t in draft.tracks}
        return {
            "ok": True,
            "name": draft.name,
            "path": str(draft.dir),
            "duration": round(draft.declared_duration, 3),
            "max_end": round(draft.max_end, 3),
            "fps": draft.fps,
            "track_count": len(draft.tracks),
            "tracks": tracks,
            "video_segments": len(draft.video_segments),
            "text_segments": len(draft.text_segments),
            "audio_segments": len(draft.audio_segments),
        }

    def tool_learn_template(self, args: dict) -> dict:
        """从现有草稿逆向提取文本样式 / 动画 / 关键帧 / 转场 / 滤镜模板 -> JSON。

        只读、零外呼。产出可直接喂给 add_styled_text / add_text_animation 的模板描述。
        """
        name = (args.get("draft_name") or self.active_draft or "").strip()
        if not name:
            return {"ok": False, "error": "未指定草稿，也没有活跃草稿"}
        try:
            draft = Draft.from_name(name)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"加载草稿失败：{exc}"}

        mats = draft.content.get("materials") or {}
        anim_index = {a.get("id"): a for a in (mats.get("material_animations") or [])
                      if isinstance(a, dict)}

        text_samples: list[dict] = []
        for seg in draft.text_segments:
            mat = seg.material or {}
            style_block: dict = {}
            raw_content = mat.get("content")
            if isinstance(raw_content, str):
                try:
                    style_block = json.loads(raw_content)
                except ValueError:
                    style_block = {}
            styles = style_block.get("styles") or [{}]
            s0 = styles[0] if styles else {}
            font = (s0.get("font") or {})
            strokes = s0.get("strokes") or []
            # 解析本段引用的动画（extra_material_refs -> material_animations）
            anims = []
            for ref in (seg.raw.get("extra_material_refs") or []):
                pack = anim_index.get(ref)
                if pack:
                    for a in (pack.get("animations") or []):
                        anims.append({"name": a.get("name"), "type": a.get("type"),
                                      "duration": round((a.get("duration") or 0) / 1_000_000, 3)})
            clip = seg.raw.get("clip") or {}
            text_samples.append({
                "text": seg.text,
                "t": seg.t, "len": seg.length,
                "font": font.get("path") or font.get("id"),
                "size": s0.get("size"),
                "bold": s0.get("bold"),
                "italic": s0.get("italic"),
                "color": (((s0.get("fill") or {}).get("content") or {}).get("solid") or {}).get("color"),
                "stroke_color": (((strokes[0].get("content") or {}).get("solid") or {}).get("color")) if strokes else None,
                "stroke_width": strokes[0].get("width") if strokes else None,
                "line_spacing": mat.get("line_spacing"),
                "line_max_width": mat.get("line_max_width"),
                "alignment": mat.get("alignment"),
                "global_alpha": mat.get("global_alpha"),
                "transform": clip.get("transform"),
                "scale": clip.get("scale"),
                "animations": anims,
            })

        # 汇总"主流样式"成一张可复用模板
        def _mode(key: str):
            vals = [t[key] for t in text_samples if t.get(key) is not None]
            # 不可哈希（如 list 颜色）转成可比较的 repr，再回映射
            keys = [v if isinstance(v, (str, int, float, bool)) else repr(v) for v in vals]
            if not keys:
                return None
            top = max(set(keys), key=keys.count)
            return vals[keys.index(top)]

        template = {
            "font": _mode("font"), "size": _mode("size"),
            "bold": _mode("bold"), "color": _mode("color"),
            "line_spacing": _mode("line_spacing"), "alignment": _mode("alignment"),
        }
        transition_names = [t.get("name") for t in (mats.get("transitions") or []) if isinstance(t, dict)]
        filter_names = [(t.get("name") or t.get("value") or "")
                        for t in (mats.get("effects") or []) if isinstance(t, dict)]

        return {
            "ok": True,
            "draft": name,
            "template": template,
            "transitions": transition_names,
            "filters": [f for f in filter_names if f],
            "text_samples": text_samples,
            "text_count": len(text_samples),
        }

    # ---------- v3.3 P1-3：视频理解侧只读证据工具（Cue/video-reader-mcp 思路） ----------

    def tool_get_video_info(self, args: dict) -> dict:
        """ffprobe 读视频元信息：时长/分辨率/帧率/码率/音频轨道（只读分析）。"""
        import json as _json  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        from .paths import ROOT, ffprobe  # noqa: PLC0415

        vp = (args.get("video_path") or "").strip()
        path = _resolve_video_path(vp, ROOT)
        if path is None:
            return {"ok": False, "error": f"视频不存在：{vp}"}
        cmd = [ffprobe(), "-v", "error", "-show_format", "-show_streams",
               "-of", "json", str(path)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        try:
            data = _json.loads(proc.stdout or "{}")
        except ValueError:
            return {"ok": False, "error": f"ffprobe 解析失败：{(proc.stderr or '')[:200]}"}
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        vids = [s for s in streams if s.get("codec_type") == "video"]
        auds = [s for s in streams if s.get("codec_type") == "audio"]
        v = vids[0] if vids else {}
        return {
            "ok": True, "video": str(path),
            "duration": round(float(fmt.get("duration", 0)), 3),
            "bit_rate": fmt.get("bit_rate"),
            "size": v.get("width") and {"width": v.get("width"), "height": v.get("height")},
            "fps": _frac(v.get("avg_frame_rate") or v.get("r_frame_rate")),
            "video_codec": v.get("codec_name"),
            "audio_tracks": [{"codec": a.get("codec_name"),
                              "sample_rate": a.get("sample_rate"),
                              "channels": a.get("channels")} for a in auds],
        }

    def tool_detect_video_scenes(self, args: dict) -> dict:
        """场景切换检测（ffmpeg scene），返回场景列表 [{start,end,duration}]。"""
        from . import vision  # noqa: PLC0415
        from .paths import ROOT  # noqa: PLC0415

        vp = (args.get("video_path") or "").strip()
        path = _resolve_video_path(vp, ROOT)
        if path is None:
            return {"ok": False, "error": f"视频不存在：{vp}"}
        thr = float(args.get("threshold", 0.3))
        cuts = vision.detect_cuts(path, thr)
        duration = vision.duration_of(path)
        bounds = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
        scenes = [{"start": round(bounds[i], 3), "end": round(bounds[i + 1], 3),
                   "duration": round(bounds[i + 1] - bounds[i], 3)}
                  for i in range(len(bounds) - 1)]
        return {"ok": True, "video": str(path), "threshold": thr,
                "duration": round(duration, 3), "scene_count": len(scenes),
                "scenes": scenes}

    def tool_extract_video_frame(self, args: dict) -> dict:
        """提取指定时间戳的一帧，返回保存路径（分析用途）。"""
        from .paths import ROOT, WORK, ffmpeg  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        vp = (args.get("video_path") or "").strip()
        path = _resolve_video_path(vp, ROOT)
        if path is None:
            return {"ok": False, "error": f"视频不存在：{vp}"}
        t = float(args.get("timestamp", 0.0))
        out_dir = WORK / "frames"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"frame-{abs(hash((str(path), t))) & 0xffffff:06x}-{t:.2f}.jpg"
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{max(t, 0):.3f}", "-i", str(path),
               "-frames:v", "1", "-vf", "scale=480:-2", str(out)]
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if not out.exists() or out.stat().st_size == 0:
            return {"ok": False, "error": f"抽帧失败：t={t}s"}
        return {"ok": True, "video": str(path), "timestamp": t,
                "frame_path": str(out), "bytes": out.stat().st_size}

    def tool_read_video_subtitles(self, args: dict) -> dict:
        """读视频旁挂的字幕（同名 .srt/.vtt）；没有则提示用 transcribe.py 生成（不自动跑 whisper）。"""
        from .paths import ROOT, TRANSCRIPTS  # noqa: PLC0415

        vp = (args.get("video_path") or "").strip()
        path = _resolve_video_path(vp, ROOT)
        if path is None:
            return {"ok": False, "error": f"视频不存在：{vp}"}
        stem = Path(path).stem
        # 候选：视频同目录 + TRANSCRIPTS 目录
        cands = [Path(path).with_suffix(".srt"), Path(path).with_suffix(".vtt"),
                 TRANSCRIPTS / f"{stem}.srt", TRANSCRIPTS / f"{stem}.vtt"]
        found = next((c for c in cands if c.exists()), None)
        if found is None:
            return {"ok": True, "video": str(path), "has_subtitles": False,
                    "note": "未找到旁挂字幕；如需逐字稿请先跑 scripts/transcribe.py（faster-whisper），"
                            "产物落 02-transcript/"}
        lines = found.read_text(encoding="utf-8", errors="ignore").splitlines()
        return {"ok": True, "video": str(path), "has_subtitles": True,
                "subtitle_file": str(found), "lines": len(lines),
                "preview": "\n".join(lines[:12])}

    # ---------- v3.4 P1：草稿只读审计/概览工具（对照 capcut-cli info/lint/materials） ----------

    def _load_draft_or_error(self, args: dict):
        """返回 (draft, None) 或 (None, err_dict)；统一草稿名解析。"""
        name = (args.get("draft_name") or self.active_draft or "").strip()
        if not name:
            return None, {"ok": False,
                          "error": "未指定草稿，也没有活跃草稿（先 list_drafts / set_active_draft）"}
        try:
            return Draft.from_name(name), None
        except Exception as exc:  # noqa: BLE001
            return None, {"ok": False, "error": f"加载草稿失败：{exc}"}

    def tool_get_draft_summary(self, args: dict) -> dict:
        """草稿概览：分辨率/帧率/时长/各轨道片段数/素材库存统计（只读，不改草稿）。"""
        draft, err = self._load_draft_or_error(args)
        if err:
            return err
        cc = draft.content.get("canvas_config") or {}
        mats = draft.content.get("materials") or {}

        def _cnt(key: str) -> int:
            v = mats.get(key)
            return len(v) if isinstance(v, list) else 0

        return {
            "ok": True,
            "draft": draft.name,
            "path": str(draft.dir),
            "duration_sec": round(draft.declared_duration, 3),
            "max_end_sec": round(draft.max_end, 3),
            "fps": draft.fps,
            "canvas": {"width": cc.get("width"), "height": cc.get("height"),
                       "ratio": cc.get("ratio")},
            "track_count": len(draft.tracks),
            "tracks": [{"type": t.type, "segments": len(t.segments)} for t in draft.tracks],
            "material_inventory": {
                "videos": _cnt("videos"), "audios": _cnt("audios"),
                "images": _cnt("images"), "texts": _cnt("texts"),
                "effects": _cnt("effects"), "video_effects": _cnt("video_effects"),
                "transitions": _cnt("transitions"), "stickers": _cnt("stickers"),
                "animations": _cnt("material_animations"),
            },
            "video_segments": len(draft.video_segments),
            "audio_segments": len(draft.audio_segments),
            "text_segments": len(draft.text_segments),
        }

    def tool_validate_draft(self, args: dict) -> dict:
        """只读校验草稿：明文嗅探/悬空素材引用/本地文件存在性/时间单位/同轨重叠，返回问题清单。

        对照 capcut-cli lint（missing-file / missing-material / caption-overlap 等）的低成本子集。
        """
        name = (args.get("draft_name") or self.active_draft or "").strip()
        if not name:
            return {"ok": False, "error": "未指定草稿，也没有活跃草稿"}
        content_path = PROJECT_DRAFTS / name / "draft_content.json"
        issues: list[dict] = []

        def _result(plaintext: bool) -> dict:
            errs = len([i for i in issues if i["severity"] == "error"])
            warns = len(issues) - errs
            return {"ok": True, "draft": name, "plaintext": plaintext,
                    "issue_count": len(issues), "issues": issues,
                    "summary": {"error": errs, "warning": warns}}

        # 1) 明文嗅探（剪映 6.0+ 可能 AES 加密，加密串不以 '{' 开头）
        if not content_path.exists():
            return {"ok": False, "error": f"草稿不存在：{name}",
                    "available": list_draft_names()}
        try:
            head = content_path.read_bytes()[:64]
        except OSError as exc:
            return {"ok": False, "error": f"读取草稿失败：{exc}"}
        plaintext = bool(head) and head.lstrip()[:1] == b"{"
        if not plaintext:
            issues.append({"severity": "error", "code": "not-plaintext",
                           "message": "draft_content.json 非明文（缺失或已被剪映 6.0+ AES 加密），无法解析校验"})
            return _result(False)

        try:
            draft = Draft.from_name(name)
        except Exception as exc:  # noqa: BLE001
            issues.append({"severity": "error", "code": "parse-failed",
                           "message": f"draft_content.json 解析失败：{exc}"})
            return _result(True)

        # 2) 顶层时长单位合理性（草稿内部为微秒：7.2s≈7267000；<100000 疑似秒被误写）
        top_dur_us = draft.content.get("duration") or 0
        if 0 < top_dur_us < 100_000:
            issues.append({"severity": "warning", "code": "duration-unit-suspect",
                           "message": f"顶层 duration={top_dur_us}（µs）<100000，疑似单位应为秒"})

        # 3) 悬空素材引用 / 片段时间单位 / 同轨重叠
        for t in draft.tracks:
            prev_end: float | None = None
            for idx, seg in enumerate(t.segments):
                if seg.material_id and draft.material(seg.material_id) is None:
                    issues.append({"severity": "error", "code": "dangling-material-ref",
                                   "track": t.type, "segment_index": idx,
                                   "message": f"片段 {idx} material_id={seg.material_id} 在素材表中找不到"})
                if t.type in ("video", "audio", "text"):
                    dur_us = (seg.raw.get("target_timerange") or {}).get("duration") or 0
                    if 0 < dur_us < 100_000:
                        issues.append({"severity": "warning", "code": "segment-duration-unit-suspect",
                                       "track": t.type, "segment_index": idx,
                                       "message": f"片段 {idx} target_timerange.duration={dur_us}（µs）<100000，疑似秒被误写"})
                if prev_end is not None and seg.t < prev_end - 1e-6:
                    issues.append({"severity": "warning", "code": "track-overlap",
                                   "track": t.type, "segment_index": idx,
                                   "message": f"片段 {idx} (t={seg.t:.2f}s) 与前一段 (end={prev_end:.2f}s) 时间重叠"})
                prev_end = seg.end

        # 4) 本地素材文件存在性
        mats = draft.content.get("materials") or {}
        for kind in ("videos", "audios", "images"):
            for m in mats.get(kind) or []:
                if not isinstance(m, dict):
                    continue
                p = m.get("path") or ""
                if not p:
                    continue
                if not Path(p).exists():
                    issues.append({"severity": "error", "code": "missing-file",
                                   "material_kind": kind.rstrip("s"),
                                   "material_name": m.get("material_name") or m.get("name"),
                                   "message": f"{kind.rstrip('s')} 素材文件不存在：{p}"})

        # 5) 空草稿
        if not draft.tracks or sum(len(t.segments) for t in draft.tracks) == 0:
            issues.append({"severity": "warning", "code": "empty-draft",
                           "message": "草稿没有任何轨道或片段"})

        return _result(True)

    def tool_list_draft_materials(self, args: dict) -> dict:
        """列出草稿引用的全部素材文件（视频/音频/图片），带 exists 存在性标记（只读）。"""
        draft, err = self._load_draft_or_error(args)
        if err:
            return err
        out: list[dict] = []
        mats = draft.content.get("materials") or {}
        for kind in ("videos", "audios", "images"):
            for m in mats.get(kind) or []:
                if not isinstance(m, dict):
                    continue
                p = m.get("path") or ""
                item = {"kind": kind.rstrip("s"), "path": p,
                        "name": m.get("material_name") or m.get("name"),
                        "exists": bool(p) and Path(p).exists()}
                if kind == "videos":
                    item["width"] = m.get("width")
                    item["height"] = m.get("height")
                    item["duration_sec"] = round((m.get("duration") or 0) / 1_000_000, 3)
                out.append(item)
        missing = sum(1 for x in out if not x["exists"])
        return {"ok": True, "draft": draft.name, "total": len(out),
                "missing": missing, "materials": out}

    def tool_get_draft_timeline(self, args: dict) -> dict:
        """返回简化时间线：每轨每段的 start/end/duration/文本或素材名（只读）。"""
        draft, err = self._load_draft_or_error(args)
        if err:
            return err
        tracks_out: list[dict] = []
        for t in draft.tracks:
            segs = []
            for idx, seg in enumerate(t.segments):
                item = {"index": idx, "start": round(seg.t, 3),
                        "end": round(seg.end, 3), "duration": round(seg.length, 3)}
                if seg.track_type == "text":
                    item["text"] = (seg.text or "")[:60]
                else:
                    item["material"] = seg.material_name() or seg.material_path()
                segs.append(item)
            tracks_out.append({"type": t.type, "segment_count": len(segs), "segments": segs})
        return {"ok": True, "draft": draft.name,
                "duration_sec": round(draft.declared_duration, 3),
                "track_count": len(draft.tracks), "tracks": tracks_out}

    # ---------- v5.2.0：视频理解 + 风格定位（智能成片闭环前置） ----------

    def tool_analyze_video(self, args: dict) -> dict:
        """素材理解：语义档案（主角/场景/叙事摘要/高光候选/时间轴）+ 可选镜头档案。

        复用 semantic_profile.build（转写自动探测 02-transcript/，全程本地优先）。
        返回结构化摘要，可直接喂给 match_style / generate_draft。
        """
        from .paths import ROOT  # noqa: PLC0415
        from . import semantic_profile  # noqa: PLC0415

        vp = (args.get("video_path") or "").strip()
        path = _resolve_video_path(vp, ROOT)
        if path is None:
            return {"ok": False, "error": f"视频不存在：{vp}"}
        use_cloud = bool(args.get("use_cloud", False))
        force = bool(args.get("force", False))
        with_shotprof = bool(args.get("with_shotprof", False))
        try:
            doc = semantic_profile.build(path, use_cloud=use_cloud, force=force)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"语义档案失败：{type(exc).__name__}: {exc}"[:400]}
        out: dict = {
            "ok": True, "video": str(path),
            "主角": doc.get("主角"), "场景": doc.get("场景"),
            "适合结构": doc.get("适合结构"),
            "叙事摘要": doc.get("叙事摘要"),
            "关键信息点": doc.get("关键信息点") or [],
            "高光候选数": len(doc.get("高光候选") or []),
            "时间轴段数": len(doc.get("叙事时间轴") or []),
            "字段完整率": doc.get("字段完整率"),
            "cache_used": bool(doc.get("_cache")),
        }
        if with_shotprof:
            try:
                from . import shotprof  # noqa: PLC0415
                prof = shotprof.build(path, use_cloud=use_cloud)
                out["shotprof"] = {
                    "镜头数": len(prof.get("shots") or []),
                    "摘要": prof.get("summary") or prof.get("镜头摘要"),
                    "重复组": len(prof.get("dup_groups") or []),
                }
            except Exception as exc:  # noqa: BLE001
                out["shotprof"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        return out

    def tool_match_style(self, args: dict) -> dict:
        """风格定位：按素材内容推荐 Top3 剪辑风格（基于语义档案+转写）。

        传 video_path 自动理解并推荐；或传 semantic_doc（JSON 字符串）直接推荐。
        """
        from .paths import ROOT  # noqa: PLC0415
        from . import semantic_profile, stylematch  # noqa: PLC0415

        vp = (args.get("video_path") or "").strip()
        semantic_doc: dict | None = None
        video_path = None
        if vp:
            path = _resolve_video_path(vp, ROOT)
            if path is None:
                return {"ok": False, "error": f"视频不存在：{vp}"}
            video_path = path
            try:
                semantic_doc = semantic_profile.build(path, use_cloud=bool(args.get("use_cloud", False)))
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"语义档案失败：{type(exc).__name__}: {exc}"[:400]}
        else:
            raw = (args.get("semantic_doc") or "").strip()
            if not raw:
                return {"ok": False, "error": "需要 video_path 或 semantic_doc 至少其一"}
            try:
                semantic_doc = json.loads(raw)
            except ValueError:
                return {"ok": False, "error": "semantic_doc 不是合法 JSON"}

        res = stylematch.match_style(semantic_doc=semantic_doc)
        recs = res.get("推荐") or []
        platform = (args.get("platform") or "douyin").strip()
        content_type = res.get("内容类型") or (recs[0].get("名称") if recs else "")
        route_hint = None
        try:
            import json as _json, os as _os
            _rp = _os.path.join(ROOT, "content-library", "platform_style_router.json")
            if _os.path.exists(_rp):
                _rt = _json.load(open(_rp, encoding="utf-8"))
                _ct = _rt.get("内容类型→风格智能匹配") or _rt.get("内容类型→风格") or {}
                _sid = None
                for k, v in _ct.items():
                    if k in str(content_type) or str(content_type) in k:
                        _sid = v; break
                if not _sid and recs:
                    _sid = recs[0].get("id")
                _plat = _rt.get("平台特性矩阵", {}).get(platform, {})
                route_hint = {
                    "目标平台": _plat.get("名称", platform),
                    "推荐风格id": _sid,
                    "完播甜点": _plat.get("完播甜点", ""),
                    "平台参数调整": _plat.get("参数调整", {}),
                    "智能调整规则": _rt.get("智能调整规则", {}),
                }
        except Exception as exc:
            route_hint = {"warn": f"路由附加失败: {type(exc).__name__}"}
        return {
            "ok": True,
            "video": str(video_path) if video_path else None,
            "匹配方式": res.get("匹配方式"),
            "使用的输入": res.get("使用的输入"),
            "内容类型": res.get("内容类型"),
            "top": [
                {"id": r.get("id"), "名称": r.get("名称"),
                 "置信度": r.get("置信度"), "理由": r.get("理由")}
                for r in recs
            ],
            "平台智能路由": route_hint,
        }

    # ---------- 技能调用 ----------

    def resolve_draft_name(self, args: dict) -> str | None:
        name = (args.get("draft_name") or "").strip() or self.active_draft
        return name or None

    def call_skill(self, skill_id: str, args: dict) -> dict:
        skill = skills.get(skill_id)
        if skill is None:
            return {"ok": False, "error": f"未知技能：{skill_id}"}

        dry_run = bool(args.get("dry_run", False))
        # 元参数不下发给技能 fn（技能按自己的 args 取值，多给键也无害，但剥干净更安全）
        fn_args = {k: v for k, v in args.items()
                   if k not in ("draft_name", "dry_run")}

        missing = [k for k in (skill.required or []) if k not in fn_args]
        if missing:
            return {"ok": False, "error": f"参数不全：{', '.join(missing)}"}
        if dry_run and (skill.channel == 'production' or skill_id == 'generate_draft'):
            return {'ok': True, 'status': 'dry_run', 'dry_run': True,
                    'note': '演算模式，不调用规划或制作接口'}

        # 解析草稿
        draft_name = self.resolve_draft_name(args)
        draft: Draft | None = None
        needs_draft = skill.channel in _WRITE_CHANNELS or skill.channel == "query" \
            or skill.channel == "shell"
        if skill_id == 'generate_draft' and fn_args.get('video'):
            needs_draft = False
        if needs_draft and skill.id not in _NO_DRAFT_NEEDED:
            if not draft_name:
                return {"ok": False, "error": f"技能 {skill_id} 需要草稿上下文，"
                                              f"请传 draft_name 或先 set_active_draft"}
            try:
                draft = Draft.from_name(draft_name)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"加载草稿失败：{exc}"}

        video = _infer_video(draft)
        transcript = skills.load_transcript(video.stem) if video else None
        before = draft.clone() if draft else None

        # 写操作先快照（和 agent._execute_step 一致）
        snapshot_id = None
        if not dry_run and skill.channel in _WRITE_CHANNELS and draft is not None:
            try:
                from . import snapshot as snap_mod
                snapshot_id = snap_mod.snapshot(draft.dir, note=f"{skill_id} 前")["id"]
            except Exception as exc:  # noqa: BLE001
                _log(f"快照失败（继续执行）：{exc}")

        ctx = skills.Context(
            draft=draft, before=before, video=video, transcript=transcript,
            dry_run=dry_run, log=lambda m: _log(str(m)),
            production_approvals=self.production_approvals,
        )
        try:
            report = skill.run(ctx, fn_args)
        except Exception as exc:  # noqa: BLE001
            _log(f"{skill_id} 执行异常：{traceback.format_exc()}")
            report = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}

        # 草稿被改了就回写（非 dry_run）
        if not dry_run and skill.channel in _WRITE_CHANNELS and ctx.draft is not None:
            try:
                ctx.draft.save()
            except Exception as exc:  # noqa: BLE001
                report = dict(report or {})
                report["save_error"] = str(exc)[:300]

        # 出片类技能会产生新草稿，把活跃草稿切过去
        produced = report.get("result_draft") if isinstance(report, dict) else None
        if produced and Path(produced).exists():
            self.active_draft = Path(produced).name

        if isinstance(report, dict):
            report.setdefault("ok", report.get("error") is None and report.get("ok") is not False)
            report["active_draft"] = self.active_draft
            if snapshot_id:
                report["snapshot"] = snapshot_id
        return report if isinstance(report, dict) else {"ok": True, "value": report}


# 会话/管理工具（不在 REGISTRY 里，由 Server 自己提供）
META_TOOLS = {
    "list_drafts": {
        "description": "列出 06-drafts/ 下所有可用草稿名，并返回当前活跃草稿",
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": "_meta_list_drafts",
    },
    "set_active_draft": {
        "description": "切换当前活跃草稿；之后不传 draft_name 的工具都会作用于它",
        "schema": {
            "type": "object",
            "properties": {"draft_name": {"type": "string", "description": "草稿名"}},
            "required": ["draft_name"],
        },
        "handler": "_meta_set_active_draft",
    },
    "draft_status": {
        "description": "读草稿状态：时长 / 帧率 / 各轨道片段数（只读，不改草稿）",
        "schema": {
            "type": "object",
            "properties": {"draft_name": {"type": "string", "description": "草稿名，默认用活跃草稿"}},
            "required": [],
        },
        "handler": "_meta_draft_status",
    },
    "learn_template_from_draft": {
        "description": "从现有草稿逆向提取文本样式/动画/关键帧/转场/滤镜模板，输出可复用 JSON（只读，零外呼）",
        "schema": {
            "type": "object",
            "properties": {"draft_name": {"type": "string", "description": "草稿名，默认用活跃草稿"}},
            "required": [],
        },
        "handler": "_meta_learn_template",
    },
    # ---- v3.3 P1-3：视频理解侧只读证据工具 ----
    "get_video_info": {
        "description": "读视频元信息：时长/分辨率/帧率/码率/音频轨道（ffprobe，只读分析）",
        "schema": {
            "type": "object",
            "properties": {"video_path": {"type": "string", "description": "视频路径（绝对或相对工程根）"}},
            "required": ["video_path"],
        },
        "handler": "_meta_get_video_info",
    },
    "detect_video_scenes": {
        "description": "场景切换检测（ffmpeg scene），返回场景列表 [{start,end,duration}]",
        "schema": {
            "type": "object",
            "properties": {"video_path": {"type": "string", "description": "视频路径"},
                          "threshold": {"type": "number", "description": "scene 阈值，默认 0.3"}},
            "required": ["video_path"],
        },
        "handler": "_meta_detect_video_scenes",
    },
    "extract_video_frame": {
        "description": "提取指定时间戳的一帧到 work/frames/，返回文件路径（分析用途）",
        "schema": {
            "type": "object",
            "properties": {"video_path": {"type": "string", "description": "视频路径"},
                          "timestamp": {"type": "number", "description": "时间戳（秒）"}},
            "required": ["video_path", "timestamp"],
        },
        "handler": "_meta_extract_video_frame",
    },
    "read_video_subtitles": {
        "description": "读视频旁挂字幕（同名 .srt/.vtt 或 02-transcript/）；没有则提示用 transcribe.py 生成",
        "schema": {
            "type": "object",
            "properties": {"video_path": {"type": "string", "description": "视频路径"}},
            "required": ["video_path"],
        },
        "handler": "_meta_read_video_subtitles",
    },
    # ---- v3.4 P1：草稿只读审计/概览（对照 capcut-cli info/lint/materials） ----
    "get_draft_summary": {
        "description": "草稿概览：分辨率(canvas)/帧率/时长/各轨片段数/素材库存统计（只读，不改草稿）",
        "schema": {
            "type": "object",
            "properties": {"draft_name": {"type": "string", "description": "草稿名，默认用活跃草稿"}},
            "required": [],
        },
        "handler": "_meta_get_draft_summary",
    },
    "validate_draft": {
        "description": "只读校验草稿：明文嗅探/悬空素材引用/本地素材文件存在性/时间单位合理性/同轨片段重叠，返回问题清单",
        "schema": {
            "type": "object",
            "properties": {"draft_name": {"type": "string", "description": "草稿名，默认用活跃草稿"}},
            "required": [],
        },
        "handler": "_meta_validate_draft",
    },
    "list_draft_materials": {
        "description": "列出草稿引用的全部素材文件（视频/音频/图片），带 exists 存在性标记（只读）",
        "schema": {
            "type": "object",
            "properties": {"draft_name": {"type": "string", "description": "草稿名，默认用活跃草稿"}},
            "required": [],
        },
        "handler": "_meta_list_draft_materials",
    },
    "get_draft_timeline": {
        "description": "返回简化时间线：每轨每段的 start/end/duration/文本或素材名（只读）",
        "schema": {
            "type": "object",
            "properties": {"draft_name": {"type": "string", "description": "草稿名，默认用活跃草稿"}},
            "required": [],
        },
        "handler": "_meta_get_draft_timeline",
    },
    # ---- v5.2.0：视频理解 + 风格定位（智能成片闭环前置） ----
    "analyze_video": {
        "description": "素材理解：语义档案（主角/场景/叙事摘要/高光候选/时间轴）+ 可选镜头档案；返回结构化摘要，可直接喂给 match_style / generate_draft",
        "schema": {
            "type": "object",
            "properties": {
                "video_path": {"type": "string", "description": "视频路径（绝对或相对工程根）"},
                "use_cloud": {"type": "boolean", "description": "允许云端视觉（默认 false 本地 Ollama）"},
                "with_shotprof": {"type": "boolean", "description": "同时生成镜头档案（较慢，默认 false）"},
                "force": {"type": "boolean", "description": "忽略缓存重算（默认 false）"},
            },
            "required": ["video_path"],
        },
        "handler": "_meta_analyze_video",
    },
    "match_style": {
        "description": "风格定位：按素材内容推荐 Top3 剪辑风格（传 video_path 自动理解，或传 semantic_doc JSON 直接推荐）",
        "schema": {
            "type": "object",
            "properties": {
                "video_path": {"type": "string", "description": "视频路径（绝对或相对工程根）"},
                "semantic_doc": {"type": "string", "description": "语义档案 JSON 字符串（video_path 与 semantic_doc 至少其一）"},
                "use_cloud": {"type": "boolean", "description": "允许云端视觉（默认 false）"},
            },
            "required": [],
        },
        "handler": "_meta_match_style",
    },
}


def _skill_to_tool(skill: skills.Skill) -> dict:
    """把一个 Skill 转成 MCP tool 描述。"""
    if skill.channel == 'production':
        from .maker_api import tool_definitions
        spec = next(t['function'] for t in tool_definitions() if t['function']['name'] == skill.id)
        schema = spec['parameters']
        schema['properties']['dry_run'] = {'type': 'boolean', 'description': '演算，不执行制作'}
        return {'name': skill.id, 'description': skill.goal + '；' + skill.notes,
                'inputSchema': schema}
    props: dict = {}
    for k, desc in (skill.args or {}).items():
        props[k] = {"type": "string", "description": desc}
        if skill.id == 'generate_draft' and k == 'publish':
            props[k]['type'] = 'boolean'
    # 每个技能工具都可选地覆盖草稿 / 试跑
    props["draft_name"] = {"type": "string",
                           "description": "草稿名；不传则用当前活跃草稿"}
    props["dry_run"] = {"type": "boolean",
                        "description": "试跑模式：只演算不落盘，默认 false"}
    required = list(skill.required or [])
    desc = skill.goal
    if skill.notes:
        desc += f"\n备注：{skill.notes}"
    if skill.sample:
        desc += f"\n示例：{json.dumps(skill.sample, ensure_ascii=False)}"
    return {
        "name": skill.id,
        "description": desc,
        "inputSchema": {"type": "object", "properties": props, "required": required},
    }


class McpServer:
    def __init__(self, production_approvals=None) -> None:
        self.state = _State(production_approvals=production_approvals)
        self._build_tools()

    def _build_tools(self) -> None:
        self.tools: dict[str, dict] = {}
        # 1) 注册表技能（动态注册）
        for skill in skills.catalog():
            self.tools[skill.id] = {"is_skill": True, "def": _skill_to_tool(skill)}
        # 2) 会话/管理工具
        for name, spec in META_TOOLS.items():
            self.tools[name] = {"is_skill": False, "def": {
                "name": name, "description": spec["description"],
                "inputSchema": spec["schema"],
            }}

    # ---------- JSON-RPC 分发 ----------

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method", "")
        msgid = msg.get("id")
        params = msg.get("params") or {}

        # 请求（有 id）才回包；通知（无 id）不回
        is_request = msgid is not None

        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [t["def"] for t in self.tools.values()]}
            elif method == "tools/call":
                # Writers may print progress; never mix it into JSON-RPC stdout.
                with redirect_stdout(sys.stderr):
                    result = self._tool_call(params)
            else:
                if is_request:
                    return self._err(msgid, -32601, f"未知方法：{method}")
                return None  # 通知类（initialized / cancelled 等）静默
        except Exception as exc:  # noqa: BLE001
            _log(f"处理 {method} 出错：{traceback.format_exc()}")
            if is_request:
                return self._err(msgid, -32000, f"{type(exc).__name__}: {exc}"[:500])
            return None

        if not is_request:
            return None
        return {"jsonrpc": "2.0", "id": msgid, "result": result}

    def _initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _tool_call(self, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        entry = self.tools.get(name)
        if entry is None:
            return self._tool_error(f"未知工具：{name}")
        try:
            if entry["is_skill"]:
                report = self.state.call_skill(name, args)
            elif name == "list_drafts":
                report = self.state.tool_list_drafts(args)
            elif name == "set_active_draft":
                report = self.state.tool_set_active_draft(args)
            elif name == "draft_status":
                report = self.state.tool_draft_status(args)
            elif name == "learn_template_from_draft":
                report = self.state.tool_learn_template(args)
            elif name == "get_video_info":
                report = self.state.tool_get_video_info(args)
            elif name == "detect_video_scenes":
                report = self.state.tool_detect_video_scenes(args)
            elif name == "extract_video_frame":
                report = self.state.tool_extract_video_frame(args)
            elif name == "read_video_subtitles":
                report = self.state.tool_read_video_subtitles(args)
            elif name == "get_draft_summary":
                report = self.state.tool_get_draft_summary(args)
            elif name == "validate_draft":
                report = self.state.tool_validate_draft(args)
            elif name == "list_draft_materials":
                report = self.state.tool_list_draft_materials(args)
            elif name == "get_draft_timeline":
                report = self.state.tool_get_draft_timeline(args)
            elif name == "analyze_video":
                report = self.state.tool_analyze_video(args)
            elif name == "match_style":
                report = self.state.tool_match_style(args)
            else:
                return self._tool_error(f"未知工具：{name}")
        except Exception as exc:  # noqa: BLE001
            _log(f"工具 {name} 异常：{traceback.format_exc()}")
            return self._tool_error(f"{type(exc).__name__}: {exc}"[:500])

        text = json.dumps(report, ensure_ascii=False)
        is_error = bool(isinstance(report, dict) and report.get("ok") is False)
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    @staticmethod
    def _tool_error(message: str) -> dict:
        return {"content": [{"type": "text", "text": message}], "isError": True}

    @staticmethod
    def _err(msgid, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msgid,
                "error": {"code": code, "message": message}}

    # ---------- 主循环 ----------

    def serve(self) -> None:
        # stdout 只放 JSON-RPC；先把编码钉死 utf-8
        try:
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        _log(f"就绪：已注册 {len(self.tools)} 个工具（{len(skills.catalog())} 个技能 + "
             f"{len(META_TOOLS)} 个管理工具）")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                resp = self._err(None, -32700, f"JSON 解析失败：{exc}")
                self._send(resp)
                continue
            resp = self.handle(msg)
            if resp is not None:
                self._send(resp)

    @staticmethod
    def _send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="aicut MCP Server（stdio）")
    parser.add_argument("--transport", default="stdio", choices=["stdio"],
                        help="传输方式，目前仅支持 stdio")
    parser.add_argument('--production-request', help='由应用/用户确认的制作请求ID')
    parser.add_argument('--approve-request-sha256', help='对应的用户确认哈希，不是模型工具参数')
    args = parser.parse_args()
    if bool(args.production_request) != bool(args.approve_request_sha256):
        parser.error('制作请求ID和用户确认哈希必须同时提供')
    approvals = ({args.production_request: args.approve_request_sha256}
                 if args.production_request else {})
    try:
        from . import cli
        cli.setup()
    except Exception:  # noqa: BLE001
        pass
    McpServer(production_approvals=approvals).serve()


if __name__ == "__main__":
    main()
