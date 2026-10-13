"""模板化剪辑引擎（v3.7 模板化剪辑引擎 · 模块3）。

从"最佳参考视频"指纹提炼一份**剪辑模板**（节奏骨架/技术参数/结构套路），
再映射到当前素材，产出 auto_edit.py 可直接消费的参数字典。

关键纪律：
  · 只学"剪辑结构特征"（节奏、钩子套路、字幕密度、关键帧模式），
    **不搬运参考视频的画面内容**——内容永远来自素材自己的语义档案；
  · 模板持久化到 content-library/templates/<id>.json，可复用、可版本化。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from .paths import CONTENT_LIBRARY, ROOT, python_exe

TEMPLATES_DIR = CONTENT_LIBRARY / "templates"


# ---------------------------------------------------------------- 模板提炼
def extract_template(reference_fp: dict, *, template_id: str | None = None,
                     template_name: str | None = None) -> dict:
    """从参考指纹提炼剪辑模板。"""
    r = reference_fp or {}
    rhythm = r.get("rhythm", {}) or {}
    structure = r.get("structure", {}) or {}
    sub = r.get("subtitle", {}) or {}
    rtype = r.get("video_type", "other")

    avg = float(rhythm.get("avg_shot") or 3.5)
    std = float(rhythm.get("shot_std") or 1.0)
    # 由 avg/std 反推 min/max：经验区间 [avg-1.5σ, avg+1.5σ]，夹到安全范围
    min_shot = round(max(1.2, avg - std * 1.2), 2)
    max_shot = round(min(8.0, avg + std * 1.2 + 1.0), 2)
    target_avg = round(avg, 2)

    if not template_id:
        template_id = f"{rtype}-template-{date.today().strftime('%Y%m%d')}"
    if not template_name:
        template_name = f"{rtype}·{structure.get('hook_type','?')}钩子-{rhythm.get('pace','?')}节奏"

    hook_text = structure.get("hook_text") or ""
    ending_text = structure.get("ending_text") or ""
    kps = (r.get("semantic", {}) or {}).get("关键信息点") or []
    src_video = r.get("video", "")

    return {
        "template_id": template_id,
        "template_name": template_name,
        "source_video": src_video,
        "source_fingerprint_summary": {
            "video_type": rtype,
            "duration": r.get("duration"),
            "avg_shot": target_avg,
            "pace": rhythm.get("pace"),
            "shot_count": rhythm.get("shot_count"),
        },
        "created_at": date.today().isoformat(),
        "structure": {
            "hook_type": structure.get("hook_type", "other"),
            "hook_lead_seconds": 2.0 if structure.get("hook_type") not in ("other", "场景式") else 3.0,
            "body_rhythm": structure.get("body_rhythm", "other"),
            "ending_type": structure.get("ending_type", "other"),
            "section_count": max(2, min(5, len(kps) or 3)),
        },
        "rhythm": {
            "target_avg_shot": target_avg,
            "target_shot_std": round(std, 2),
            "pace": rhythm.get("pace", "中速"),
            "min_shot": min_shot,
            "max_shot": max_shot,
        },
        "techniques": {
            "subtitle_density": sub.get("density", 0.85),
            "subtitle_max_chars": int(round(sub.get("avg_chars_per_line") or 14)),
            "visual_element_density": float(r.get("visual_density") or 0.3),
            "transition": "硬切",
            "filter": "",
            "opening_effect": "光晕",
            "keyframe_mode": "slow-zoom",
        },
        "style_hint": rtype,
        "description": (
            f"从参考视频提炼：{structure.get('hook_type','?')}开头（{hook_text[:14]}）→ "
            f"{structure.get('body_rhythm','?')}讲解 → {structure.get('ending_type','?')}结尾（{ending_text[:14]}）；"
            f"{rhythm.get('pace','?')}节奏，均镜{target_avg}s"
        ),
        "_source_hook_text": hook_text,
        "_source_ending_text": ending_text,
    }


# ---------------------------------------------------------------- 模板应用
def apply_template(template: dict, material_fp: dict, *, video_path: str | None = None) -> dict:
    """把模板映射到当前素材，返回 auto_edit 可消费的参数字典。

    原则：结构/节奏/技术参数学参考；封面文案、CTA、内容用素材自己的语义。
    """
    t = template or {}
    rhythm = t.get("rhythm", {}) or {}
    tech = t.get("techniques", {}) or {}
    struct = t.get("structure", {}) or {}
    mat_sem = (material_fp or {}).get("semantic", {}) or {}

    avg = float(rhythm.get("target_avg_shot") or 3.5)
    min_s = float(rhythm.get("min_shot") or max(1.5, avg - 1.0))
    max_s = float(rhythm.get("max_shot") or avg + 1.5)

    # 封面/CTA 提示用素材自己的关键信息点生成
    kps = mat_sem.get("关键信息点") or []
    cover_hint = str(kps[0]) if kps else (mat_sem.get("叙事摘要") or [""])[0]
    cta_map = {"koubo-zhishi": "收藏这条，月子里用得上",
               "duibi": "收藏这份对比，选品不踩坑",
               "jiaocheng": "收藏跟着做，一步不落",
               "qingxu-gushi": "评论区说说你的经历"}
    cta_hint = cta_map.get(t.get("style_hint", ""), "收藏这条，用得上")

    return {
        "style": t.get("style_hint", "koubo-zhishi"),
        "short_seconds": round(min_s, 2),
        "long_seconds": round(max_s, 2),
        "transition": tech.get("transition", "硬切"),
        "filter": tech.get("filter", ""),
        "subtitle_max_chars": max(8, int(tech.get("subtitle_max_chars") or 14)),
        "keyframes": tech.get("keyframe_mode", "slow-zoom"),
        "hook_lead": float(struct.get("hook_lead_seconds") or 2.0),
        "cover_hint": cover_hint[:20],
        "cta_hint": cta_hint,
        "notes": (f"结构学参考：{struct.get('hook_type','?')}开头→"
                  f"{struct.get('body_rhythm','?')}→{struct.get('ending_type','?')}；"
                  f"内容用素材自己的语义，不搬运参考画面"),
        "template_used": t.get("template_id"),
    }


# ---------------------------------------------------------------- 持久化
def save_template(template: dict, path: str | Path | None = None) -> Path:
    if path is None:
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        path = TEMPLATES_DIR / f"{template.get('template_id', 'template')}.json"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(template, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_template(template_id_or_path: str) -> dict:
    """支持两种入参：模板 ID（在 content-library/templates/ 下找）或直接给文件路径。"""
    cand = Path(template_id_or_path)
    if not cand.exists():
        cand = TEMPLATES_DIR / f"{template_id_or_path}.json"
    if not cand.exists():
        raise FileNotFoundError(f"模板不存在: {template_id_or_path}")
    return json.loads(cand.read_text(encoding="utf-8-sig"))


def list_templates() -> list[dict]:
    out = []
    if not TEMPLATES_DIR.exists():
        return out
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
            out.append({"template_id": d.get("template_id", p.stem),
                        "template_name": d.get("template_name", ""),
                        "style_hint": d.get("style_hint", ""),
                        "path": str(p)})
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------------------------------------------------------------- 端到端
def build_and_edit(video: str | Path, reference_fp: dict, *, name: str | None = None,
                   publish: bool = False) -> dict:
    """端到端：提炼模板 → 应用 → 调 auto_edit 出片（剪映草稿）。"""
    video = Path(video)
    from . import videofinger  # 延迟导入

    mat_fp = videofinger.build(video)
    tmpl = extract_template(reference_fp)
    applied = apply_template(tmpl, mat_fp, video_path=str(video))
    tpath = save_template(tmpl)

    # 用 subprocess 调 auto_edit.py，把模板参数透传
    out_name = name or f"{video.stem}-templated"
    cmd = [python_exe(), str(ROOT / "scripts" / "auto_edit.py"),
           "--video", str(video),
           "--template", str(tpath),
           "--name", out_name,
           "--semantic"]
    if publish:
        cmd.append("--publish")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(ROOT))
    return {
        "video": str(video),
        "material_fingerprint": mat_fp,
        "reference_fingerprint": reference_fp,
        "template": tmpl,
        "template_path": str(tpath),
        "applied_params": applied,
        "auto_edit_returncode": proc.returncode,
        "auto_edit_stdout_tail": (proc.stdout or "")[-1500:],
        "auto_edit_stderr_tail": (proc.stderr or "")[-800:],
    }


if __name__ == "__main__":  # 简单自检：列模板
    for t in list_templates():
        print(t)
