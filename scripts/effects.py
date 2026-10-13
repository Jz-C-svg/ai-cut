r"""画面特效与花字：按片段添加视频特效、滤镜、转场，给文字挂花字。

视频特效和滤镜由草稿库按名称解析（VideoSceneEffectType / VideoCharacterEffectType / FilterType）；
花字需要真实的素材条目，来自本机历史草稿提取出的 config/effects_catalog.json。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from JianYingDraft import (  # noqa: E402
    FilterType, TransitionType, VideoCharacterEffectType, VideoSceneEffectType,
)

CATALOG_FILE = ROOT / "config" / "effects_catalog.json"


def load_catalog() -> dict:
    if not CATALOG_FILE.exists():
        return {}
    return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))


def resolve_effect(name: str):
    for cls in (VideoSceneEffectType, VideoCharacterEffectType):
        member = getattr(cls, name, None)
        if member is not None:
            return member
    return None


def resolve_filter(name: str):
    return getattr(FilterType, name, None)


def resolve_transition(name: str):
    return getattr(TransitionType, name, None)


def search(kind: str, keyword: str, limit: int = 20) -> list[str]:
    cls = {"特效": VideoSceneEffectType, "人物特效": VideoCharacterEffectType,
           "滤镜": FilterType, "转场": TransitionType}[kind]
    return [n for n in cls.__members__ if keyword in n][:limit]


def apply_text_effect(draft_dir: Path, resource_id: str, target: str = "cover",
                      catalog: dict | None = None) -> int:
    """给指定文字挂上花字。target 取 cover 或 all。"""
    catalog = catalog if catalog is not None else load_catalog()
    entry = catalog.get(str(resource_id)) or catalog.get(resource_id)
    if not entry:
        raise KeyError(f"花字素材库里没有 {resource_id}，先运行 scripts/harvest_effects.py")
    material = json.loads(json.dumps(entry["material"]))
    new_id = str(uuid.uuid4()).upper()
    material["id"] = new_id

    content_path = draft_dir / "draft_content.json"
    data = json.loads(content_path.read_text(encoding="utf-8"))
    mats = data.setdefault("materials", {})
    mats.setdefault("effects", []).append(material)

    # 找出要挂花字的文字片段的素材 id
    wanted = []
    for track in data.get("tracks", []):
        if track.get("type") != "text":
            continue
        if target != "all" and track.get("name") != target:
            continue
        for seg in track.get("segments", []):
            wanted.append(seg.get("material_id"))
            seg.setdefault("extra_material_refs", []).append(new_id)

    touched = 0
    for text in mats.get("texts", []):
        if text.get("id") not in wanted:
            continue
        payload = json.loads(text["content"])
        for style in payload.get("styles", []):
            style["effectStyle"] = {"id": str(material["resource_id"]), "path": material.get("path", "")}
        text["content"] = json.dumps(payload, ensure_ascii=False)
        touched += 1
    if touched:
        content_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return touched


def available_text_effects(catalog: dict | None = None) -> list[dict]:
    """Return only text effects whose local cache assets are present and usable."""
    catalog = catalog if catalog is not None else load_catalog()
    found = []
    for rid, entry in catalog.items():
        material = entry.get("material") or {}
        if material.get("type") != "text_effect":
            continue
        path = Path(str(material.get("path") or ""))
        if path.is_dir() and (path / "effectStyle.json").is_file():
            found.append({"id": str(rid), "name": str(material.get("name") or ""),
                          "path": str(path), "source": str(entry.get("source") or "")})
    return sorted(found, key=lambda item: (item["name"].casefold(), item["id"]))
