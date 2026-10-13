r"""验证环：判断"我刚做的这一步到底成没成"。

三类断言：
  结构  草稿 IR 的前后差异是否符合预期（时长、片段数、最小片段、无重叠、位移…）
  视觉  截图差异是否落在预期区域（用于界面通道）
  语义  关键帧交给视觉模型判断（可选，慢，必要时才用）

主体的可靠性就建立在"每步都能被证伪"上。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .draft import Draft


@dataclass
class Check:
    name: str
    ok: bool
    expect: Any = None
    actual: Any = None
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        extra = f" | {self.detail}" if self.detail else ""
        return f"[{mark}] {self.name}: 期望 {self.expect} / 实际 {self.actual}{extra}"


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, check: Check) -> "Report":
        self.checks.append(check)
        return self

    def lines(self) -> list[str]:
        head = "验证通过" if self.ok else "验证失败"
        passed = sum(c.ok for c in self.checks)
        return [f"{head}（{passed}/{len(self.checks)}）"] + [c.line() for c in self.checks]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "checks": [c.__dict__ for c in self.checks]}


def check_duration_delta(before: Draft, after: Draft, low: float, high: float) -> Check:
    delta = round(after.declared_duration - before.declared_duration, 4)
    ok = low - 1e-6 <= delta <= high + 1e-6
    return Check("时长变化", ok, f"[{low:+.3f}, {high:+.3f}]s", f"{delta:+.3f}s",
                 f"{before.declared_duration:.3f}s -> {after.declared_duration:.3f}s")


def check_duration_abs(after: Draft, low: float, high: float) -> Check:
    d = round(after.declared_duration, 4)
    return Check("成片时长", low <= d <= high, f"[{low:.2f}, {high:.2f}]s", f"{d:.2f}s")


def check_segment_count(after: Draft, track: str, low: int | None = None, high: int | None = None) -> Check:
    n = len(after.segments_of(track))
    lo = low if low is not None else (high or 0)
    hi = high if high is not None else (low or 0)
    return Check(f"{track} 片段数", lo <= n <= hi, f"[{lo}, {hi}]", n)


def check_min_segment_len(after: Draft, track: str, min_len: float) -> Check:
    segs = after.segments_of(track)
    shortest = min((s.length for s in segs), default=0.0)
    return Check(f"{track} 最短片段", shortest >= min_len - 1e-6, f">= {min_len:.2f}s", f"{shortest:.2f}s")


def check_no_overlap(after: Draft, track: str | None = None) -> Check:
    bad: list[str] = []
    for t in after.tracks:
        if track and t.type != track:
            continue
        prev_end = -1.0
        for s in t.segments:
            if s.t < prev_end - 0.004:
                bad.append(f"{t.type}#{s.id[:6]}@{s.t:.2f}")
            prev_end = max(prev_end, s.end)
    return Check("同轨无重叠", not bad, "无重叠", "; ".join(bad) or "无")


def check_text_present(after: Draft, text: str) -> Check:
    hit = any(text in (s.text or "") for s in after.text_segments)
    return Check(f"字幕包含「{text[:12]}」", hit, True, hit)


def check_text_absent(after: Draft, text: str) -> Check:
    hit = any(text in (s.text or "") for s in after.text_segments)
    return Check(f"字幕不含「{text[:12]}」", not hit, True, not hit)


def check_shift(before: Draft, after: Draft, delta: float, tol: float = 0.02) -> Check:
    """未变形片段是否整体位移了 delta（"删除后整体前移"这类断言）。"""
    bmap = {s.id: s for s in before.all_segments()}
    shifts = [round(s.t - bmap[s.id].t, 4) for s in after.all_segments()
              if s.id in bmap and abs(s.length - bmap[s.id].length) < 1e-4]
    if not shifts:
        return Check("整体位移", False, f"{delta:+.3f}s", "没有可比片段")
    hit = sum(1 for s in shifts if abs(s - delta) <= tol)
    return Check("整体位移", hit == len(shifts), f"{delta:+.3f}s x{len(shifts)}",
                 f"命中 {hit}/{len(shifts)}", f"样本 {shifts[:6]}")


def check_track_exists(after: Draft, track: str) -> Check:
    present = track in after.track_types
    return Check(f"{track} 轨存在", present, True, present)


def check_screen_changed(png_a: str | Path, png_b: str | Path, min_ratio: float = 0.002,
                         region: tuple[int, int, int, int] | None = None) -> Check:
    """界面通道用：截图前后是否真的变了，变了多少。"""
    from PIL import Image, ImageChops

    a, b = Image.open(png_a).convert("RGB"), Image.open(png_b).convert("RGB")
    if region:
        a, b = a.crop(region), b.crop(region)
    if a.size != b.size:
        return Check("界面变化", False, f">= {min_ratio:.1%}", f"尺寸不同 {a.size} vs {b.size}")
    diff_img = ImageChops.difference(a, b).convert("L")
    pixels = list(diff_img.getdata())
    changed = sum(1 for p in pixels if p > 24)
    ratio = changed / max(len(pixels), 1)
    return Check("界面变化", ratio >= min_ratio, f">= {min_ratio:.1%}", f"{ratio:.2%}")


def from_spec(spec: dict, before: Draft | None, after: Draft) -> Report:
    """按声明式规格跑一组断言。

    规格示例：
      {"duration_delta": [-2.05, -1.95], "no_overlap": true,
       "min_segment_len": {"video": 0.8}, "shift": -2.0,
       "text_present": ["24小时"], "text_absent": ["三二一"]}
    """
    rep = Report()
    if "duration_delta" in spec and before is not None:
        low, high = spec["duration_delta"]
        rep.add(check_duration_delta(before, after, float(low), float(high)))
    if "duration_abs" in spec:
        low, high = spec["duration_abs"]
        rep.add(check_duration_abs(after, float(low), float(high)))
    if "segment_count" in spec:
        for track, bounds in spec["segment_count"].items():
            rep.add(check_segment_count(after, track, int(bounds[0]), int(bounds[1])))
    if "min_segment_len" in spec:
        for track, value in spec["min_segment_len"].items():
            rep.add(check_min_segment_len(after, track, float(value)))
    if spec.get("no_overlap"):
        rep.add(check_no_overlap(after, spec.get("no_overlap_track")))
    if "shift" in spec and before is not None:
        rep.add(check_shift(before, after, float(spec["shift"]), float(spec.get("shift_tol", 0.02))))
    for text in spec.get("text_present", []):
        rep.add(check_text_present(after, text))
    for text in spec.get("text_absent", []):
        rep.add(check_text_absent(after, text))
    for track in spec.get("track_exists", []):
        rep.add(check_track_exists(after, track))
    return rep


def extract_frame(video: str | Path, t: float, out: str | Path | None = None) -> Path:
    """从视频里取 t 秒处的一帧（语义断言的输入）。"""
    from .paths import WORK, ffmpeg

    video = Path(video)
    dst = Path(out) if out else WORK / "ui" / "frames" / f"{video.stem}-{t:.2f}.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg(), "-hide_banner", "-nostats", "-loglevel", "error", "-y",
         "-ss", f"{max(t, 0):.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(dst)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return dst


def check_semantic(video: str | Path, t: float, question: str, *, model: str | None = None,
                   out: str | Path | None = None) -> Check:
    """让本地视觉模型看一眼某时刻的画面，回答一个 YES/NO 问题。

    用在"结构对了但画面不对"的场景，例如：出片后首帧里到底有没有人。
    慢（本地视觉模型 3-5s 热 / 冷启动更久），所以只在关键节点用。
    """
    from . import llm

    try:
        frame = extract_frame(video, t, out)
        if not frame.exists():
            return Check(f"语义：{question[:18]}", False, "能取到帧", "取帧失败", str(video))
        answer = llm.vision_describe(frame, f"{question}\n只回答 YES 或 NO，后面跟一句理由。", model=model)
    except Exception as exc:  # noqa: BLE001
        return Check(f"语义：{question[:18]}", False, "模型能回答", f"{type(exc).__name__}", str(exc)[:120])
    head = (answer or "").strip().upper()
    ok = head.startswith("YES") or head.startswith("是")
    return Check(f"语义：{question[:18]}", ok, "YES", head[:120], detail=str(frame))


def check_segment_count_delta(before: Draft, after: Draft, track: str, delta: int) -> Check:
    n0 = len(before.segments_of(track))
    n1 = len(after.segments_of(track))
    return Check(f"{track} 段数变化", (n1 - n0) == delta, f"{delta:+d}", f"{n1 - n0:+d}",
                 f"{n0} -> {n1}")


def check_duration_same(before: Draft, after: Draft, tol: float = 0.02) -> Check:
    d = round(after.declared_duration - before.declared_duration, 4)
    return Check("总时长不变", abs(d) <= tol, f"±{tol:.2f}s", f"{d:+.3f}s")


def from_spec2(spec: dict, before: "Draft | None", after: "Draft") -> Report:
    """from_spec 的增强版：支持"段数变化""时长不变"这类增量断言。

    主体统一走这个入口，这样新增精确原语（切分/加字幕/加转场）都能被客观验收。
    """
    rep = from_spec(spec, before, after)
    if before is not None:
        for track, delta in (spec.get("segment_count_delta") or {}).items():
            rep.add(check_segment_count_delta(before, after, track, int(delta)))
        if spec.get("duration_same"):
            rep.add(check_duration_same(before, after, float(spec.get("duration_same_tol", 0.02))))
    return rep


# ---------------------------------------------------------------------------
# v3.2 A5：Stop Gate（吸收自 Rushes 思路）——每步执行前的草稿合法性闸门
# ---------------------------------------------------------------------------
# 评估结论：Rushes 的 insert/delete/update/split 四原子操作与现有 edit2.py
# （split_at / remove_segment / add_subtitle / ripple_delete）高度重叠，不重复引入；
# SQLite 版本管理与现有 snap_mod.snapshot + memory.sqlite episodes 重叠，也不重复引入。
# 只吸收 Rushes 的 **Stop Gate** 思路：agent 执行每步前，检查草稿是否仍处于合法状态，
# 不合法则停止并报错（防止一步把草稿搞坏还继续往下做）。


def stop_gate_check(draft: "Draft") -> dict:
    """执行前检查草稿是否仍合法。返回 ``{passed, violations, warnings}``。

    检查四项：
      ① 轨道无重叠   —— 同轨片段时间区间不互相压盖；
      ② 片段无越界   —— 每个片段 end <= 声明时长 + 1s 容差，且时长 > 0；
      ③ 素材引用     —— 主素材 material_id 必须存在（硬阻断）；
                        extra_material_refs 缺失记 warning（剪映草稿里 text 片段常
                        引用全局动画/花字资源，不在本 draft materials 内、由剪映自行
                        解析，属正常容忍条件，不阻断）；
      ④ 时长非负     —— declared_duration >= 0。

    轻量纯检查，不修改草稿。passed 只由硬问题决定；warnings 供信息性观察。
    """
    violations: list[str] = []
    warnings: list[str] = []
    if draft is None:
        return {"passed": False, "violations": ["草稿为 None"], "warnings": []}

    # ④ 时长非负
    dur = draft.declared_duration
    if dur < -1e-6:
        violations.append(f"声明时长为负: {dur:.3f}s")

    # 素材 id 全集（一次索引）
    mat_ids: set[str] = set()
    for items in (draft.content.get("materials") or {}).values():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    mat_ids.add(it["id"])

    # ① 轨道无重叠 + ② 片段无越界/零长 + ③ 素材引用
    tol = 1.0  # 越界容差 1s（结尾余量）
    for t in draft.tracks:
        prev_end = -1.0
        for s in t.segments:
            if s.length <= 1e-4:
                violations.append(f"零长片段 {t.type}#{s.id[:6]} t={s.t:.2f} len={s.length:.3f}")
            if s.end > dur + tol:
                violations.append(f"越界片段 {t.type}#{s.id[:6]} end={s.end:.2f} > 时长={dur:.2f}")
            if s.t < prev_end - 0.004:
                violations.append(f"轨道重叠 {t.type}#{s.id[:6]} t={s.t:.2f} 压前序 end={prev_end:.2f}")
            prev_end = max(prev_end, s.end)
            # ③ 主素材存在（硬阻断——主素材缺失会导致黑帧）
            if s.material_id and s.material_id not in mat_ids:
                violations.append(f"悬空主素材 {t.type}#{s.id[:6]} material_id={s.material_id[:8]}")
            # ③ extra_material_refs 缺失（warning——全局动画/花字资源剪映自行解析）
            for rid in (s.raw.get("extra_material_refs") or []):
                if rid not in mat_ids:
                    warnings.append(f"外部引用 {t.type}#{s.id[:6]} ref={str(rid)[:8]}（剪映全局资源）")

    return {"passed": len(violations) == 0, "violations": violations, "warnings": warnings,
            "duration": round(dur, 3), "segments": sum(len(t.segments) for t in draft.tracks)}
