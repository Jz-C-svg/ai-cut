#!/usr/bin/env python3
r"""v3.1 从可核实来源提取真实 resourceId，写入 config/resource_registry.json。

来源（只提取源码/草稿里实际存在的 ID，绝不编造）：
  1) external/refs/capcut-mate/src/pyJianYingDraft/metadata/  各枚举（直接 import，最可靠）
  2) lib/JianYingDraft/metadata/                               项目自带枚举（sync_from_jianyingdraft）
  3) 真实草稿：06-drafts/月子知识-成片/draft_content.json
     + JianyingPro 草稿目录下所有 draft_content.json（materials.* 的 resource_id）
  4) external/refs/capcut-cli-renezander/src/catalogue.ts 与 pintoj71-capcut-mcp
     （这两个是 TS/生成物，多为类别索引，若不含裸 ID 则如实记录，不硬凑）

用法:
  python scripts/extract_resource_ids.py
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut.paths import CONFIG_DIR, PROJECT_DRAFTS  # noqa: E402
from aicut.resource_registry import (  # noqa: E402
    ResourceRegistry, entries_from_enum_module,
)

CAPCUT_MATE_SRC = ROOT / "external" / "refs" / "capcut-mate" / "src"
JY_PROJECTS = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "JianyingPro" / \
    "User Data" / "Projects" / "com.lveditor.draft"

# 草稿 materials.<list> -> 注册表类别
DRAFT_MATERIAL_TO_CATEGORY = {
    "video_effects": "effects",
    "effects": "effects",
    "flowers": "text_styles",
    "text_templates": "text_templates",
    "stickers": "stickers",
    "transitions": "transitions",
    "filters": "filters",
}


def from_capcut_mate(reg: ResourceRegistry) -> dict:
    src_tag = "capcut_mate"
    if not CAPCUT_MATE_SRC.exists():
        return {"skipped": f"路径不存在 {CAPCUT_MATE_SRC}"}
    sys.path.insert(0, str(CAPCUT_MATE_SRC))
    try:
        import pyJianYingDraft.metadata as meta  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"skipped": f"导入失败: {e}"}
    mapping = [
        ("FontType", "fonts", "font_meta.py"),
        ("FilterType", "filters", "filter_meta.py"),
        ("TransitionType", "transitions", "transition_meta.py"),
        ("VideoSceneEffectType", "effects", "video_scene_effect.py"),
        ("VideoCharacterEffectType", "effects", "video_character_effect.py"),
        ("TextIntro", "text_intro", "text_intro.py"),
        ("TextOutro", "text_outro", "text_outro.py"),
        ("TextLoopAnim", "text_loop", "text_loop.py"),
        ("IntroType", "video_intro", "video_intro.py"),
        ("OutroType", "video_outro", "video_outro.py"),
        ("GroupAnimationType", "video_group_anim", "video_group_animation.py"),
        ("AudioSceneEffectType", "audio_scene", "audio_scene_effect.py"),
        ("ToneEffectType", "audio_tone", "tone_effect.py"),
        ("SpeechToSongType", "speech_song", "speech_to_song.py"),
        ("MixModeType", "mix_modes", "mix_mode_meta.py"),
        ("MaskType", "masks", "mask_meta.py"),
    ]
    n = 0
    for entry in entries_from_enum_module(meta, mapping, src_tag):
        reg.add_resource(entry)
        n += 1
    return {"added": n}


def from_project_draft(reg: ResourceRegistry, draft_json: Path) -> dict:
    """解析单个 draft_content.json，提取 materials 下真实使用的 resource_id。"""
    if not draft_json.exists():
        return {"skipped": f"不存在 {draft_json}"}
    draft_name = draft_json.parent.name
    try:
        data = json.loads(draft_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"skipped": f"非纯净JSON {draft_name}"}
    mats = data.get("materials", {})
    if not isinstance(mats, dict):
        return {"skipped": "materials 不是对象"}
    n = 0
    for mat_key, category in DRAFT_MATERIAL_TO_CATEGORY.items():
        items = mats.get(mat_key)
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            rid = str(it.get("resource_id") or "")
            if not rid:
                continue
            eid = str(it.get("effect_id") or "")
            name = str(it.get("name") or it.get("value") or "")
            entry = {
                "id": f"{category}:{rid}",
                "name": name,
                "category": category,
                "resource_id": rid,
                "effect_id": eid,
                "source": [f"draft:{draft_name}"],
                "verified_at": "",
                "notes": f"materials.{mat_key}",
            }
            reg.add_resource(entry)
            n += 1
    return {"added": n, "draft": draft_name}


def from_real_jy_drafts(reg: ResourceRegistry) -> dict:
    if not JY_PROJECTS.exists():
        return {"skipped": f"目录不可访问 {JY_PROJECTS}"}
    files = glob.glob(str(JY_PROJECTS / "*" / "draft_content.json"))
    n = 0
    ok = 0
    for f in files:
        r = from_project_draft(reg, Path(f))
        n += r.get("added", 0)
        ok += 1
    return {"scanned_drafts": ok, "added": n}


def note_catalogue_and_mcp() -> dict:
    """catalogue.ts / capcut-mcp 多为生成物或类别索引，如实说明，不硬凑 ID。"""
    out = {}
    ts = ROOT / "external" / "refs" / "capcut-cli-renezander" / "src" / "catalogue.ts"
    out["catalogue_ts_exists"] = ts.exists()
    out["catalogue_ts_note"] = (
        "catalogue.ts 引用生成的 enums.js/enums.json，本身不写裸 resource_id，"
        "其数据与 capcut-mate 重叠，不重复收录。"
    )
    mcp = ROOT / "external" / "refs" / "pintoj71-capcut-mcp" / "capcut_mcp"
    out["capcut_mcp_exists"] = mcp.exists()
    out["capcut_mcp_note"] = "MCP 服务封装层，无独立静态 ID 表。"
    return out


def main() -> int:
    reg = ResourceRegistry()
    report: dict = {}

    print("[1/4] capcut-mate 外部枚举 ...")
    report["capcut_mate"] = from_capcut_mate(reg)
    print("   ", report["capcut_mate"])

    print("[2/4] 项目 lib/JianYingDraft 枚举同步 ...")
    report["jianyingdraft"] = reg.sync_from_jianyingdraft()
    print("   ", report["jianyingdraft"])

    print("[3/4] 真实草稿（项目 06-drafts + JianyingPro）...")
    own = from_project_draft(reg, PROJECT_DRAFTS / "月子知识-成片" / "draft_content.json")
    real = from_real_jy_drafts(reg)
    report["project_draft"] = own
    report["real_jy_drafts"] = real
    print("   项目草稿:", own)
    print("   剪映草稿:", real)

    print("[4/4] 其它来源说明 ...")
    report["other_sources"] = note_catalogue_and_mcp()
    print("   ", report["other_sources"])

    out = reg.save()
    stats = reg.stats()
    report["stats"] = stats
    print("\n=== 写入", out, "===")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
