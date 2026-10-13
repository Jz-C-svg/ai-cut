r"""技能库：主体的"手"。

每个技能 = 一段可复用的操作 + 它自己的验收规格。主体的规划器只能从这张表里挑
技能、填参数——不允许自由发挥，这样每一步都能被验证、被回滚、被统计成功率。

通道：
  draft  直接改草稿 JSON（精确、快）
  ui     驱动剪映界面（工程文件做不到的事）
  shell  调用既有流水线脚本（auto_edit / build_draft 等）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import edit
from . import messages
from . import perception as _perception
from .draft import Draft
from .memory import Memory
from .paths import SHEETS, TRANSCRIPTS, WORK, ffmpeg


@dataclass
class Context:
    """一次任务里所有技能共享的上下文。"""

    draft: Draft | None = None
    before: Draft | None = None
    video: Path | None = None
    transcript: dict | None = None
    vision: dict | None = None
    memory: Memory | None = None
    dry_run: bool = False
    log: Callable[[str], None] = print
    production_approvals: dict = field(default_factory=dict)
    production_options: dict = field(default_factory=dict)
    goal: str = ''

    @property
    def duration(self) -> float:
        return self.draft.declared_duration if self.draft else 0.0


@dataclass
class Skill:
    id: str
    goal: str
    channel: str
    args: dict[str, str] = field(default_factory=dict)
    sample: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    verify: dict = field(default_factory=dict)
    fn: Callable[[Context, dict], dict] | None = None
    notes: str = ""

    def catalog_line(self) -> str:
        arg_text = "、".join(f"{k}（{v}）" for k, v in self.args.items()) or "无参数"
        return f"- {self.id}｜{self.goal}｜参数：{arg_text}｜示例 {json.dumps(self.sample, ensure_ascii=False)}"

    def run(self, ctx: Context, args: dict) -> dict:
        if self.fn is None:
            raise NotImplementedError(self.id)
        return self.fn(ctx, args)


REGISTRY: dict[str, Skill] = {}


def register(skill: Skill) -> Skill:
    REGISTRY[skill.id] = skill
    return skill


def catalog() -> list[Skill]:
    return list(REGISTRY.values())


def catalog_text() -> str:
    return "\n".join(s.catalog_line() for s in catalog())


def get(skill_id: str) -> Skill | None:
    return REGISTRY.get(skill_id)


# ---------- 素材层工具 ----------


LAST_SILENCE_INFO: dict = {}


def _silence_cache_file(video: Path, db: float, min_len: float):
    """v3.2-B15：静音检测缓存文件。key=素材路径+大小+mtime+db+min_len，任一变化自动换名失效。"""
    try:
        st = video.stat()
        raw = f"{video.resolve()}|{st.st_size}|{int(st.st_mtime)}|{db}|{min_len}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        cache_dir = WORK / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / f"silence-{video.stem}-{digest}.json"
    except OSError:
        return None


def detect_silences(video: str | Path, db: float = -30.0, min_len: float = 0.5) -> list[tuple[float, float]]:
    """静音区间（秒）。ffmpeg silencedetect，确定性、不调模型。

    v3.2-B15：结果按 素材+mtime+参数 缓存到 work/cache/silence-<素材>-<hash>.json；
    二次运行直接读缓存，不再重复调 ffmpeg。素材改动或参数变化 -> key 变 -> 自动重测。
    """
    video = Path(video)
    if not video.exists():
        return []
    cache_file = _silence_cache_file(video, db, min_len)
    spans: list[tuple[float, float]] | None = None
    if cache_file is not None and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8-sig"))
            spans = [tuple(s) for s in data.get("spans", [])]  # type: ignore
        except Exception:  # noqa: BLE001
            spans = None
    if spans is None:
        cmd = [ffmpeg(), "-hide_banner", "-nostats", "-i", str(video),
               "-af", f"silencedetect=noise={db}dB:d={min_len}", "-f", "null", "-"]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        log = (proc.stderr or "") + (proc.stdout or "")
        starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", log)]
        ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", log)]
        spans = []
        for i, s in enumerate(starts):
            e = ends[i] if i < len(ends) else None
            if e and e > s:
                spans.append((round(s, 3), round(e, 3)))
        if cache_file is not None:
            try:
                cache_file.write_text(
                    json.dumps({"spans": [list(s) for s in spans]}, ensure_ascii=False),
                    encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
    covered = round(sum(b - a for a, b in spans), 2)
    LAST_SILENCE_INFO.clear()
    LAST_SILENCE_INFO.update({"spans": len(spans), "covered": covered})
    if spans:
        total = max(b for _, b in spans)
        if total and covered > total * 0.8:
            LAST_SILENCE_INFO["guard"] = messages.SILENCE_ALL
            return []
    return spans


def source_to_timeline(draft: Draft, src_t: float) -> float | None:
    """素材时间 -> 时间线时间（考虑裁剪与变速）。"""
    for seg in draft.video_segments:
        if seg.src_in is None or seg.src_len is None:
            continue
        if seg.src_in - 1e-6 <= src_t <= seg.src_in + seg.src_len + 1e-6:
            speed = seg.speed or 1.0
            return round(seg.t + (src_t - seg.src_in) / speed, 3)
    return None


def timeline_spans(draft: Draft, src_span: tuple[float, float]) -> tuple[float, float] | None:
    a = source_to_timeline(draft, src_span[0])
    b = source_to_timeline(draft, src_span[1])
    if a is None or b is None or b <= a:
        return None
    return (a, b)


def load_transcript(name: str) -> dict | None:
    path = TRANSCRIPTS / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return None


def load_understanding(name: str) -> dict | None:
    path = WORK / f"understand-{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return None


def segment_spans(transcript: dict) -> list[tuple[float, float, str]]:
    """逐字稿 -> [(起, 止, 文本)]，兼容不同字段名。"""
    out = []
    segs = transcript.get("segments") or transcript.get("句") or []
    for s in segs:
        start = s.get("start", s.get("起", 0)) or 0
        end = s.get("end", s.get("止", 0)) or 0
        text = (s.get("text") or s.get("句") or "").strip()
        if end > start:
            out.append((float(start), float(end), text))
    return out


def _video_of(ctx: Context) -> Path | None:
    if ctx.video and Path(ctx.video).exists():
        return Path(ctx.video)
    if ctx.draft and ctx.draft.video_segments:
        p = Path(ctx.draft.video_segments[0].material_path())
        if p.exists():
            return p
    return None


def _name_of(ctx: Context) -> str:
    v = _video_of(ctx)
    if v:
        return v.stem
    return ctx.draft.name if ctx.draft else ""


def _delete(ctx: Context, t0: float, t1: float) -> dict:
    rep = edit.ripple_delete(ctx.draft, t0, t1, dry_run=ctx.dry_run)
    gap = round(t1 - t0, 3)
    rep["verify"] = {
        "duration_delta": [-gap - 0.06, -gap + 0.06],
        "no_overlap": True,
        "shift": -gap,
        "shift_tol": 0.06,
    }
    return rep


def skill_delete_range(ctx: Context, args: dict) -> dict:
    return _delete(ctx, float(args["t0"]), float(args["t1"]))


def skill_delete_head(ctx: Context, args: dict) -> dict:
    return _delete(ctx, 0.0, float(args["seconds"]))


def skill_delete_tail(ctx: Context, args: dict) -> dict:
    seconds = float(args["seconds"])
    end = ctx.duration
    return _delete(ctx, round(end - seconds, 3), end)


def skill_delete_silences(ctx: Context, args: dict) -> dict:
    # Optional upgrade: chain retake/filler/semantic. Default args keep old behavior.
    if args.get("filler") or args.get("retake") or args.get("use_semantic"):
        from . import skills_retake  # lazy import, avoid circular
        return skills_retake.run_delete_filler_retakes(ctx, {
            "use_retake": bool(args.get("retake", True)),
            "use_filler": bool(args.get("filler", True)),
            "use_semantic": bool(args.get("use_semantic", False)),
            "filler_words": args.get("filler_words"),
            "pad_ms": args.get("pad_ms", 50),
            "with_silence": True,
            "min_len": args.get("min_len"),
            "db": args.get("db"),
        })
    min_len = float(args.get("min_len", _perception.get("silence_min_len")))
    db = float(args.get("db", _perception.get("silence_db")))
    video = _video_of(ctx)
    spans = detect_silences(video, db, min_len) if video else []
    planned = [sp for sp in (timeline_spans(ctx.draft, s) for s in spans) if sp]
    planned = [(a, b) for a, b in planned if b - a >= 0.08]
    removed = 0.0
    done: list[list[float]] = []
    for a, b in sorted(planned, reverse=True):  # 从后往前删，前面的坐标才不失效
        edit.ripple_delete(ctx.draft, a, b, dry_run=ctx.dry_run)
        removed += b - a
        done.append([a, b])
    removed = round(removed, 3)
    return {
        "op": "delete_silences", "note": (LAST_SILENCE_INFO.get("guard") or ""),
        "source_silences": [[round(a, 3), round(b, 3)] for a, b in spans],
        "applied": done,
        "removed": removed,
        "verify": {"duration_delta": [-removed - 0.1, -removed + 0.1], "no_overlap": True}
        if removed > 0 else {},
    }


def skill_delete_ai_cuts(ctx: Context, args: dict) -> dict:
    """把 AI 识别标为「删除」的句子连同画面剪掉。"""
    import difflib

    name = _name_of(ctx)
    understanding = ctx.transcript_understanding if hasattr(ctx, "transcript_understanding") else None
    understanding = understanding or load_understanding(name)
    transcript = ctx.transcript or load_transcript(name)
    if not understanding or not transcript:
        return {"op": "delete_ai_cuts", "skipped": "缺 work/understand-<素材>.json 或 02-transcript/<素材>.json",
                "verify": {}}

    spans = segment_spans(transcript)
    targets = [item.get("句", "") for item in understanding.get("逐句", [])
               if item.get("动作") == "删除" and item.get("句")]
    min_ratio = float(args.get("min_ratio", 0.6))

    hits: list[tuple[float, float, str]] = []
    for target in targets:
        best, best_ratio = None, 0.0
        for start, end, text in spans:
            ratio = difflib.SequenceMatcher(None, target, text).ratio()
            if ratio > best_ratio:
                best, best_ratio = (start, end, text), ratio
        if best and best_ratio >= min_ratio:
            mapped = timeline_spans(ctx.draft, (best[0], best[1]))
            if mapped:
                hits.append((mapped[0], mapped[1], target))

    removed = 0.0
    done = []
    for a, b, text in sorted(hits, reverse=True):
        edit.ripple_delete(ctx.draft, a, b, dry_run=ctx.dry_run)
        removed += b - a
        done.append({"range": [a, b], "句": text})
    removed = round(removed, 3)
    return {
        "op": "delete_ai_cuts",
        "marked": targets,
        "applied": done,
        "removed": removed,
        "verify": {"duration_delta": [-removed - 0.15, -removed + 0.15], "no_overlap": True}
        if removed > 0 else {},
    }


def skill_trim_to(ctx: Context, args: dict) -> dict:
    """压到目标时长：先删静音，再删 AI 判定的废话，仍不够就砍片尾。"""
    target = float(args["seconds"])
    allow_cut_tail = bool(args.get("cut_tail", True))
    current = ctx.duration
    if current <= target + 0.05:
        return {"op": "trim_to", "target": target, "removed": 0.0,
                "verify": {"duration_abs": [0.0, target + 0.05], "no_overlap": True}}

    candidates: list[tuple[float, float, str]] = []
    video = _video_of(ctx)
    if video:
        spans = detect_silences(video, float(_perception.get("silence_db")), float(_perception.get("silence_min_len")) * 0.8)
        for span in spans:
            mapped = timeline_spans(ctx.draft, span)
            if mapped:
                candidates.append((mapped[0], mapped[1], "静音"))

    # 候选按"最该删"排序：静音优先，其次靠后的内容
    candidates.sort(key=lambda c: (c[2] != "静音", -c[0]))
    to_delete = []
    planned = 0.0
    for a, b, why in candidates:
        if current - planned <= target:
            break
        to_delete.append((a, b, why))
        planned += b - a

    removed = 0.0
    done = []
    for a, b, why in sorted(to_delete, reverse=True):
        edit.ripple_delete(ctx.draft, a, b, dry_run=ctx.dry_run)
        removed += b - a
        done.append({"range": [a, b], "why": why})

    if allow_cut_tail and ctx.duration > target + 0.1:
        extra = round(ctx.duration - target, 3)
        edit.ripple_delete(ctx.draft, target, ctx.duration, dry_run=ctx.dry_run)
        removed += extra
        done.append({"range": [target, target + extra], "why": "片尾"});
    removed = round(removed, 3)
    return {
        "op": "trim_to",
        "target": target,
        "applied": done,
        "removed": removed,
        "verify": {"duration_abs": [0.0, target + 0.1], "no_overlap": True},
    }


def skill_generate_draft(ctx: Context, args: dict) -> dict:
    """Brain planning -> MakerAPI inspection; rendering waits for user confirmation."""
    import uuid
    from .maker_api import MakerAPI
    args = {**args, **ctx.production_options}
    video = Path(args["video"]) if args.get("video") else _video_of(ctx)
    if not video or not Path(video).exists():
        return {"op": "generate_draft", "ok": False, "error": "没有可用素材", "verify": {}}
    style = args.get("style", "koubo-zhishi")
    name = args.get("name") or f"{Path(video).stem}-{style}"
    task_id = 'maker-plan-' + uuid.uuid4().hex
    cmd = [python_exe(), str(SCRIPTS / 'pipeline.py'), '--video', str(video),
           '--style', style, '--name', name, '--smart', '--intent', '--no-build',
           '--task-id', task_id, '--goal', ctx.goal or args.get('goal') or '根据真实素材和台词生成剪辑方案']
    for opt, key in (("--cover", "cover"), ("--cta", "cta"), ("--bgm", "bgm")):
        if args.get(key):
            cmd += [opt, str(args[key])]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=14400,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    report = {'op': 'generate_draft', 'name': name, 'style': style, 'rc': proc.returncode,
              'stop_agent': True, 'task_id': task_id,
              'stdout_tail': (proc.stdout or '')[-600:], 'stderr_tail': (proc.stderr or '')[-400:]}
    status_path = WORK / 'tasks' / task_id / 'status.json'
    try:
        state = json.loads(status_path.read_text(encoding='utf-8'))
        sheet = (state.get('artifacts') or {}).get('working_sheet')
        if proc.returncode or state.get('state') != 'planned' or not sheet:
            raise ValueError(state.get('error') or 'Planning did not produce a current decision sheet')
        prepared = MakerAPI().prepare(sheet, args.get('software', 'jianying'),
                                      args.get('engine', 'project'), args.get('output'),
                                      args.get('publish', False))
        return {**report, **prepared, 'result_sheet': sheet,
                'note': ('大脑方案已生成并交给制作接口检查，等待确认后执行' if prepared['ok']
                         else '大脑方案未通过制作接口检查，详见阻断原因')}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {**report, 'ok': False, 'status': 'blocked', 'error': str(exc)}


register(Skill(
    id="delete_range", goal="删除时间线上任意一段并让后面的内容整体前移", channel="draft",
    args={"t0": "起点秒", "t1": "终点秒"}, sample={"t0": 0, "t1": 2.0}, required=["t0", "t1"],
    verify={"no_overlap": True}, fn=skill_delete_range,
))
register(Skill(
    id="delete_head", goal="删掉开头一段（口令、废话、空白）", channel="draft",
    args={"seconds": "删多少秒"}, sample={"seconds": 2.0}, required=["seconds"],
    verify={"no_overlap": True}, fn=skill_delete_head,
))
register(Skill(
    id="delete_tail", goal="删掉结尾一段（收尾废话、静音）", channel="draft",
    args={"seconds": "删多少秒"}, sample={"seconds": 1.5}, required=["seconds"],
    verify={"no_overlap": True}, fn=skill_delete_tail,
))
register(Skill(
    id="delete_silences", goal="剪掉所有长于阈值的停顿（连画面一起）", channel="draft",
    args={"min_len": "最短静音秒数，默认 0.5", "db": "静音阈值 dB，默认 -30"},
    sample={"min_len": 0.5}, verify={"no_overlap": True}, fn=skill_delete_silences,
))
register(Skill(
    id="delete_ai_cuts", goal="把 AI 识别标为「删除」的口误/废话连画面剪掉", channel="draft",
    args={"min_ratio": "句匹配相似度，默认 0.6"}, sample={}, verify={"no_overlap": True},
    fn=skill_delete_ai_cuts,
    notes="需要先跑 understand.py 产出 work/understand-<素材>.json 与逐字稿",
))
register(Skill(
    id="trim_to", goal="把成片压到目标时长", channel="draft",
    args={"seconds": "目标秒数", "cut_tail": "不够删时是否允许砍片尾，默认 true"},
    sample={"seconds": 20}, verify={"no_overlap": True}, required=["seconds"], fn=skill_trim_to,
))
register(Skill(
    id="generate_draft", goal="从素材理解和规划，生成待确认的制作接口请求", channel="shell",
    args={"video": "素材路径（默认取当前草稿的素材）", "style": "风格 id，如 koubo-zhishi",
          "name": "成片名", "cover": "封面文案", "cta": "行动指令",
          "goal": "自然语言剪辑目标（Agent上下文中的目标优先）",
          "software": "目标软件ID，默认jianying", "engine": "project或portable",
          "output": "独立MP4输出路径", "publish": "是否复制到剪映草稿库，默认false"},
    sample={"style": "koubo-zhishi"}, fn=skill_generate_draft,
    notes="内部走 pipeline.py --no-build，成功后调用MakerAPI检查；不自动生成或发布草稿",
))


# 放在文件末尾导入：上面几个技能实现运行时才需要这些路径（模块级导入在前或后都安全）
from .paths import CONTENT_LIBRARY, PROJECT_DRAFTS, SCRIPTS, python_exe  # noqa: E402

# ---------- 成片工作流存档（Skill Archive）接入 ----------
# 设计：content-library/skills/*.json 是"换素材即可复用"的成片工作流。
# 运行时由 load_archive() 把它包装成一个 channel="shell" 的 Skill：
#   fn = 先跑 scripts/skill_archive.py restore 生成新 sheet，再跑 build_draft.py 出草稿。
# 不破坏既有 REGISTRY / register 机制；存档 id 与内置技能冲突时跳过并告警。

ARCHIVE_DIR = CONTENT_LIBRARY / "skills"
_ARCHIVE_TAG = "__archive__"  # 标记本注册表中的技能由存档加载，便于去重


def list_archives() -> list[Path]:
    """扫描 ARCHIVE_DIR 下所有存档 JSON。"""
    if not ARCHIVE_DIR.exists():
        return []
    return sorted(p for p in ARCHIVE_DIR.glob("*.json") if p.is_file())


def _run_archive_shell(archive_path: Path):
    """返回一个 fn(ctx, args)：restore 出新 sheet -> build_draft 出草稿。"""
    def fn(ctx: Context, args: dict) -> dict:
        video = Path(args["video"]) if args.get("video") else _video_of(ctx)
        transcript = args.get("transcript")
        out_sheet = args.get("out")
        project = args.get("project")
        if not (video and transcript and out_sheet):
            return {"op": "archive_run", "ok": False,
                    "error": "需要 video / transcript / out 三个参数", "verify": {}}

        # 第一步：restore 出新决策单 sheet（复用脚本，保证唯一真相源）
        restore_cmd = [python_exe(), str(SCRIPTS / "skill_archive.py"), "restore",
                       "--archive", str(archive_path),
                       "--video", str(video),
                       "--transcript", str(transcript),
                       "--out", str(out_sheet)]
        if project:
            restore_cmd += ["--project", str(project)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        r1 = subprocess.run(restore_cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=600, env=env)
        if r1.returncode != 0:
            return {"op": "archive_run", "ok": False, "stage": "restore",
                    "rc": r1.returncode, "stdout_tail": (r1.stdout or "")[-600:],
                    "stderr_tail": (r1.stderr or "")[-400:], "verify": {}}

        # 第二步：build_draft.py 把 sheet 变成剪映草稿
        build_cmd = [python_exe(), str(SCRIPTS / "build_draft.py"), str(out_sheet)]
        if args.get("publish"):
            build_cmd.append("--publish")
        r2 = subprocess.run(build_cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=1800, env=env)
        return {
            "op": "archive_run",
            "ok": r2.returncode == 0,
            "archive": archive_path.name,
            "sheet": str(out_sheet),
            "rc": r2.returncode,
            "stdout_tail": (r2.stdout or "")[-600:],
            "stderr_tail": (r2.stderr or "")[-400:],
            "verify": {"track_exists": ["video"], "no_overlap": True},
        }
    return fn


def load_archive(archive_path: str | Path) -> Skill:
    """从一个 Skill Archive JSON 构造 Skill 对象（channel=shell）。

    不自动注册；是否注册由 register() / load_all_archives() 决定。
    """
    archive_path = Path(archive_path)
    if not archive_path.is_absolute():
        # 既支持传文件名（如 "koubo-zhishi-v1.json"），也支持传相对 ROOT 的路径
        # （如 "content-library/skills/koubo-zhishi-v1.json"）
        root = ARCHIVE_DIR.parents[1]  # ARCHIVE_DIR = ROOT/content-library/skills
        cand = root / archive_path
        archive_path = cand if cand.exists() else ARCHIVE_DIR / archive_path
    doc = json.loads(Path(archive_path).read_text(encoding="utf-8-sig"))
    sid = str(doc.get("id") or archive_path.stem)
    gates = doc.get("quality_gates") or {}
    skill = Skill(
        id=sid,
        goal=str(doc.get("description") or doc.get("name") or sid),
        channel="shell",
        args={
            "video": "新素材视频路径",
            "transcript": "新转写/字幕 JSON 路径",
            "out": "输出新决策单 sheet 路径",
            "project": "可选：新工程名，默认 <素材名>-成片",
            "publish": "可选：是否 --publish 到剪映草稿目录",
        },
        sample={"video": "work/normalized/xxx.mp4",
                "transcript": "02-transcript/xxx.auto.json",
                "out": "03-scripts/xxx-复用.sheet.json"},
        required=["video", "transcript", "out"],
        verify={"track_exists": ["video"],
                "duration_abs": [0.5, float(gates.get("max_duration_seconds", 180))]},
        fn=_run_archive_shell(archive_path),
        notes=str(doc.get("usage_example") or "")
        + "；tags=" + ",".join(doc.get("tags") or []),
    )
    setattr(skill, _ARCHIVE_TAG, True)
    return skill


def load_all_archives(register_it: bool = True) -> list[Skill]:
    """扫描 ARCHIVE_DIR，把所有存档包装成 Skill。

    register_it=True 时一并注册进 REGISTRY；与内置技能或已有存档 id 冲突时跳过该存档。
    """
    out: list[Skill] = []
    for p in list_archives():
        try:
            sk = load_archive(p)
        except Exception as e:  # noqa: BLE001
            print(f"[skills] 跳过存档 {p.name}：{e}")
            continue
        if register_it:
            if sk.id in REGISTRY and not getattr(REGISTRY[sk.id], _ARCHIVE_TAG, False):
                print(f"[skills] 存档 id 与内置技能冲突，跳过注册：{sk.id}")
                continue
            register(sk)
        out.append(sk)
    return out


def describe_skill(skill_id: str) -> dict:
    s = REGISTRY.get(skill_id)
    if not s:
        return {}
    return {"id": s.id, "goal": s.goal, "channel": s.channel, "args": s.args,
            "sample": s.sample, "notes": s.notes, "verify": s.verify}


# ---------- Editor-Critic 迭代循环的控制原语（TOP1） ----------
# 这两个技能不碰草稿，只在迭代循环内部作为 Editor/Critic 的信号：
#   DONE   Editor 侧：我改完了，请评审
#   RENDER Critic 侧：评审通过，可终态
# 规划器一般不会主动选它们；迭代循环里 review verdict==render 时由循环本身终止。

def _op_done(ctx, args):
    return {"op": "done", "ok": True, "note": "Editor 信号：改完了，请评审"}


def _op_render(ctx, args):
    return {"op": "render", "ok": True, "note": "Critic 信号：评审通过，可终态"}


register(Skill(
    id="DONE", goal="（迭代循环内部信号）Editor 表示已完成本轮修改，请 Critic 评审",
    channel="control", args={}, sample={}, required=[], verify={}, fn=_op_done,
    notes="不碰草稿；只在 Editor-Critic 迭代循环内部使用",
))
register(Skill(
    id="RENDER", goal="（迭代循环内部信号）Critic 表示评审通过，可进入终态",
    channel="control", args={}, sample={}, required=[], verify={}, fn=_op_render,
    notes="不碰草稿；只在 Editor-Critic 迭代循环内部使用",
))


def get_registry_ids():
    """返回当前已注册的全部技能 id（review_film 用它校验 suggested_action 防幻觉）。"""
    return list(REGISTRY.keys())
