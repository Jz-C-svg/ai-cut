# -*- coding: utf-8 -*-
"""Critic 硬规则层（确定性评审，零 LLM 调用）。

来源：外网方案精读（L-Storyboard 的 shot_size/angle/motion 三槽位 → 硬规则可判；
EditDuet/VEA 的 Critic 输出 schema）蒸馏落地。
原则：
  · 每个 issue 带 rule_id，可与 LLM 层（review_film）issues 合并去重
  · 规则全部可复现：同一草稿每次评审结果一致（区别于 LLM 的随机性）
  · 规则只读草稿 + 风格卡 + 素材元信息，不改任何内容
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .paths import ROOT, ffprobe

STYLES_DIR = ROOT / "content-library" / "styles"
SCRIPTS_DIR = ROOT / "03-scripts"

# 无风格卡时的通用阈值（秒 / 字）
_DEF_MIN_SHOT, _DEF_MAX_SHOT = 2.0, 8.0
_DEF_MAX_LINE = 16
_LONG_SHOT_STYLES = {"zhiyu-shuhuan"}  # 治愈舒缓：长镜头合法


def load_style_card(style_id: str | None) -> dict | None:
    if not style_id:
        return None
    p = STYLES_DIR / f"{style_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def find_style_from_sheet(draft_name: str) -> str | None:
    """从决策单（03-scripts/<草稿名>.sheet.json）反查风格 id。"""
    p = SCRIPTS_DIR / f"{draft_name}.sheet.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    sid = d.get("style") or d.get("风格")
    return sid if isinstance(sid, str) else None


def _probe_duration(video: str | Path | None) -> float | None:
    if not video:
        return None
    try:
        proc = subprocess.run(
            [ffprobe(), "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30)
        return round(float(proc.stdout.strip()), 2) if proc.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _issue(rule_id: str, severity: str, location: str, reason: str,
           plan_patch: dict | None = None) -> dict:
    return {"rule_id": rule_id, "severity": severity, "location": location,
            "reason": reason, "plan_patch": plan_patch}


def hard_rules(draft, *, video: str | Path | None = None,
               style_id: str | None = None, sheet: dict | None = None) -> list[dict]:
    """对成片草稿做确定性评审，返回 issue 列表（rule_id 唯一）。

    参数：
      draft    Draft 对象（aicut.draft）
      video    素材路径（用于时长压缩比规则；可缺省）
      style_id 风格 id（缺省从决策单反查）
      sheet    决策单 dict（可选，避免重复读盘）
    """
    issues: list[dict] = []
    name = getattr(draft, "name", "") or ""
    if not style_id:
        style_id = find_style_from_sheet(name)
    card = load_style_card(style_id)

    cp = (card or {}).get("剪辑参数") or {}
    min_shot = float(cp.get("镜头最短秒") or _DEF_MIN_SHOT)
    max_shot = float(cp.get("镜头最长秒") or _DEF_MAX_SHOT)
    max_line = int(cp.get("字幕每行最多字") or
                   (cp.get("字幕") or {}).get("每行最多字") or _DEF_MAX_LINE)

    # ---- 素材时长（用于压缩比规则）----
    src_dur = _probe_duration(video) if video else None

    # ---- 轨道数据 ----
    vsegs = list(draft.video_segments or [])
    tsegs = list(draft.text_segments or [])
    total = float(getattr(draft, "max_end", 0) or 0) or float(getattr(draft, "declared_duration", 0) or 0)

    # R1 镜头数量：少于 2 镜无法成节奏
    if len(vsegs) < 2:
        issues.append(_issue("R1", "P0", "全片",
                             f"镜头数 {len(vsegs)} < 2，无法形成剪辑节奏",
                             {"skill": "trim_to", "args": {}} if False else None))

    # R2 单镜超长（长镜风格豁免）
    if vsegs:
        max_dur = max((s.length for s in vsegs), default=0.0)
        if max_dur > max_shot * 1.8 and style_id not in _LONG_SHOT_STYLES:
            issues.append(_issue("R2", "P1", "全片",
                                 f"最长镜头 {max_dur:.1f}s 超过风格上限 {max_shot}s 的 1.8 倍，节奏拖沓"))

    # R3 平均镜头节奏 ∈ 风格区间
    if vsegs:
        avg = sum((s.length for s in vsegs)) / len(vsegs)
        if avg < min_shot * 0.6 or avg > max_shot * 1.5:
            issues.append(_issue("R3", "P1", "全片",
                                 f"平均镜头 {avg:.1f}s 超出风格区间 [{min_shot:.0f},{max_shot:.0f}]s（当前偏离 {(avg/max(min_shot,0.1)):.1f}x）"))

    # R4 字幕行宽超限
    for i, s in enumerate(tsegs):
        txt = (s.text or "").strip()
        if not txt:
            continue
        for line in txt.split("\n"):
            if len(line) > max_line:
                issues.append(_issue("R4", "P1", f"字幕{i+1}",
                                     f"字幕行 {len(line)} 字 > 风格上限 {max_line} 字：{line[:18]}…"))
                break

    # R5 开头钩子：前 3s 无字幕且无视觉文本 → 缺钩子
    if tsegs:
        hook = any(s.t < 3.0 for s in tsegs)
        if not hook:
            issues.append(_issue("R5", "P1", "0-3s",
                                 "开头 3s 无字幕钩子（结论/数字/痛点未前置），完播率风险"))

    # R6 结尾收束：最后 2s 无字幕/视觉 → 缺 CTA
    if tsegs:
        tail = any(s.t + s.length >= max(total - 2.0, 0.0) for s in tsegs)
        if not tail:
            issues.append(_issue("R6", "P2", "结尾",
                                 "结尾 2s 无收束字幕/引导（收藏、关注、预约）"))

    # R7 时长压缩比：成片超素材时长（未裁剪反而变长）
    if src_dur and total > src_dur + 2.0:
        issues.append(_issue("R7", "P0", "全片",
                             f"成片 {total:.1f}s 超过素材 {src_dur:.1f}s（+{total-src_dur:.1f}s），疑似未做删减",
                             {"skill": "trim_to", "args": {}}))

    # R8 视觉缺位：决策单声明了视觉适配但草稿无图片叠加 → 高光未可视化
    if sheet is not None:
        vplan = sheet.get("visual_plan") or []
        highlights = 0
        sem = sheet.get("semantic") or {}
        if isinstance(sem, dict):
            highlights = len(sem.get("高光候选") or [])
        if highlights and not vplan:
            issues.append(_issue("R8", "P1", "全片",
                                 f"语义档案有 {highlights} 个高光候选，但成片 0 个视觉叠加（数字卡/警示标签/结论卡未适配）"))

    # P0 必须排在前面（Editor 优先处理）
    order = {"P0": 0, "P1": 1, "P2": 2}
    issues.sort(key=lambda i: order.get(i["severity"], 3))
    return issues
