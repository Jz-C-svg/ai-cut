r"""v3.2 A1：capcut_auto 工具衔接（取增量，与 v3.1 模板逆向/资源库衔接）。

四个原语，全部基于剪映草稿 JSON 直改，不重建样式：
  - duplicate_as_template  复制片段作模板（保留样式/动画/效果，清空文本）；
  - replace_text           只改文本素材 content JSON 的 text 字段，styles 数组不动；
  - inspect_material       提取片段的花字/字体 resource_id、动画/效果 id，与 resource_registry 衔接；
  - import_track           从另一个草稿导入指定类型轨道（含其素材）。

参考：external/refs 中未找到 capcut_auto 的精确源码（duplicate_as_template /
replace_text / inspect_material / import_track 这组命名无完全对应实现）。
其中 inspect_material 的"从模板取素材信息"思路与 pyJianYingDraft 的
``ScriptFile.inspect_material`` / ``TextSegment.add_bubble/add_effect`` 一致；
replace_text 的"只动 text 不动 styles"核心来自 text_segment.py 的 content JSON 结构。
本文件按任务语义实现，并与现有 edit2.py / resource_registry.py 衔接。

时间对外用秒，内部微秒（与 edit2.py 一致）。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .draft import Draft, new_id, to_us


# ---------- 工具：按 id 找片段 ----------

def _segment_by_id(draft: Draft, seg_id: str):
    for seg in draft.all_segments():
        if seg.id == seg_id or seg.id.startswith(seg_id):
            return seg
    raise KeyError(f"找不到片段 {seg_id}")


def _material_refs_map(draft: Draft) -> dict[str, dict]:
    """material_id -> material dict 的全量索引（覆盖所有 kind）。"""
    out: dict[str, dict] = {}
    for kind, items in (draft.content.get("materials") or {}).items():
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                out[it["id"]] = it
    return out


def _deep_copy_material(draft: Draft, mat_id: str, id_map: dict[str, str]) -> dict | None:
    """深拷贝一个素材，分配新 id，加入草稿对应 kind 列表。返回新素材 dict。

    id_map 记录 old_id -> new_id，供调用方重写 extra_material_refs。
    """
    idx = _material_refs_map(draft)
    src = idx.get(mat_id)
    if src is None:
        return None
    new_mat = copy.deepcopy(src)
    new_mat["id"] = new_id()
    # 找它原来属于哪个 kind，追加进同 kind 列表
    kind = draft.material_kind(mat_id) or "texts"
    bucket = draft.content.setdefault("materials", {}).setdefault(kind, [])
    bucket.append(new_mat)
    id_map[mat_id] = new_mat["id"]
    return new_mat


# ---------- A1.1：复制片段作模板 ----------

def duplicate_as_template(draft: Draft, segment_id: str) -> str:
    """复制一个文本片段作为模板：保留所有样式/动画/效果，清空文本内容。

    - 深拷贝片段 raw + 其素材 + extra_material_refs 指向的动画/效果素材；
    - 全部分配新 id（避免与原片段素材冲突）；
    - 文本素材 content JSON 里的 text 置空，styles 数组原样保留；
    - 新片段落到原片段所在轨道，时间范围复用原片段（调用方自行移动）。

    返回新片段 id。
    """
    seg = _segment_by_id(draft, segment_id)
    track = next(t for t in draft.tracks if t.type == seg.track_type)

    id_map: dict[str, str] = {}
    # 1) 深拷贝主素材
    old_mat_id = seg.material_id
    new_mat = _deep_copy_material(draft, old_mat_id, id_map)
    if new_mat is None:
        raise KeyError(f"片段 {segment_id} 的素材 {old_mat_id} 不在 materials 中")

    # 2) 深拷贝 extra_material_refs 指向的素材（动画/花字/滤镜…）
    old_refs = list(seg.raw.get("extra_material_refs") or [])
    new_refs: list[str] = []
    for rid in old_refs:
        copied = _deep_copy_material(draft, rid, id_map)
        new_refs.append(copied["id"] if copied else rid)

    # 3) 清空文本内容（保留 styles）
    if isinstance(new_mat.get("content"), str):
        try:
            cj = json.loads(new_mat["content"])
            cj["text"] = ""
            for style in cj.get("styles") or []:
                if isinstance(style.get("range"), list) and len(style["range"]) == 2:
                    style["range"] = [0, 0]
            new_mat["content"] = json.dumps(cj, ensure_ascii=False)
        except (ValueError, TypeError):
            new_mat["content"] = json.dumps({"styles": [], "text": ""}, ensure_ascii=False)

    # 4) 深拷贝片段，换 id、换 material_id、换 refs
    new_seg = copy.deepcopy(seg.raw)
    new_seg["id"] = new_id()
    new_seg["material_id"] = new_mat["id"]
    new_seg["extra_material_refs"] = new_refs

    track.raw["segments"].append(new_seg)
    track.raw["segments"].sort(
        key=lambda s: s.get("target_timerange", {}).get("start", 0))
    draft._tracks = None
    draft._mat_index = None
    return new_seg["id"]


# ---------- A1.2：只换文本，保留格式 ----------

def replace_text(draft: Draft, segment_id: str, new_text: str) -> bool:
    """替换文本片段的文字，**保留所有格式**（字体/颜色/大小/描边/动画/阴影）。

    核心：只改文本素材 content JSON 里的 ``text`` 字段，并把 styles 的 range
    缩到新文本长度；**styles 数组一个都不动**，所以字体/颜色/描边/花字样式原样保留。
    动画/阴影等由 extra_material_refs 指向的独立素材承载，也不动。

    返回 True 表示成功；片段不是文本/素材无 content 时返回 False。
    """
    seg = _segment_by_id(draft, segment_id)
    mat = draft.material(seg.material_id)
    if not mat or not isinstance(mat.get("content"), str):
        return False
    try:
        cj = json.loads(mat["content"])
    except (ValueError, TypeError):
        return False
    if "text" not in cj and "styles" not in cj:
        return False
    new_text = str(new_text)
    cj["text"] = new_text
    # 高亮 range 随新文本长度伸缩（不重建 styles 本体）
    for style in cj.get("styles") or []:
        if isinstance(style, dict) and isinstance(style.get("range"), list) \
                and len(style["range"]) == 2:
            style["range"] = [0, len(new_text)]
    mat["content"] = json.dumps(cj, ensure_ascii=False)
    draft._mat_index = None
    return True


# ---------- A1.3：inspect_material ----------

def inspect_material(draft: Draft, segment_id: str) -> dict:
    """提取片段的花字/文本样式 resource_id、动画 id、效果 id，返回结构化信息。

    与 v3.1 resource_registry 衔接：每个 resource_id 都会尝试查注册表拿名字。
    """
    seg = _segment_by_id(draft, segment_id)
    mat = draft.material(seg.material_id) or {}

    # 注册表懒加载（查不到就空，不阻塞）
    reg = None
    try:
        from .resource_registry import ResourceRegistry
        reg = ResourceRegistry.load()
    except Exception:  # noqa: BLE001
        reg = None

    def _name_of(rid: str) -> str:
        if reg is None:
            return ""
        hit = reg.get_by_resource_id(rid)
        return str(hit.get("name", "")) if hit else ""

    result: dict[str, Any] = {
        "segment_id": seg.id,
        "track_type": seg.track_type,
        "material_id": seg.material_id,
        "font": None, "size": None, "fill_color": None,
        "flower_text": None,  # 花字
        "animations": [],
        "extra_refs": [],
    }

    # 文本样式：从 content JSON 里挖 font / size / fill / effectStyle
    if isinstance(mat.get("content"), str):
        try:
            cj = json.loads(mat["content"])
            styles = cj.get("styles") or []
            if styles:
                s0 = styles[0]
                font = s0.get("font") or {}
                if font.get("id"):
                    result["font"] = {"resource_id": font["id"],
                                      "name": _name_of(font["id"]),
                                      "path": font.get("path", "")}
                result["size"] = s0.get("size")
                fill = (s0.get("fill") or {}).get("content") or {}
                solid = (fill.get("solid") or {})
                if "color" in solid:
                    result["fill_color"] = solid["color"]
                effect_style = s0.get("effectStyle") or {}
                if effect_style.get("id"):
                    result["flower_text"] = {"resource_id": effect_style["id"],
                                             "name": _name_of(effect_style["id"])}
        except (ValueError, TypeError):
            pass

    # extra_material_refs：动画 / 滤镜 / 转场 / 花字素材
    idx = _material_refs_map(draft)
    for rid in seg.raw.get("extra_material_refs") or []:
        ref_mat = idx.get(rid)
        if not ref_mat:
            result["extra_refs"].append({"id": rid, "kind": "悬空", "name": ""})
            continue
        kind = draft.material_kind(rid) or "unknown"
        entry: dict[str, Any] = {"id": rid, "kind": kind, "name": ref_mat.get("name", "")}
        # 动画素材：拆出每个 animation 的 id / resource_id
        if ref_mat.get("type") == "sticker_animation" or "animations" in ref_mat:
            for a in ref_mat.get("animations") or []:
                if isinstance(a, dict):
                    result["animations"].append({
                        "type": a.get("type", ""),
                        "name": a.get("name", ""),
                        "effect_id": a.get("id", ""),
                        "resource_id": a.get("resource_id", ""),
                    })
        # 滤镜/转场/特效：挖 resource_id
        for key in ("resource_id", "effect_id"):
            if ref_mat.get(key):
                entry[key] = ref_mat[key]
                entry["registry_name"] = _name_of(str(ref_mat[key]))
        result["extra_refs"].append(entry)

    return result


# ---------- A1.4：import_track ----------

def import_track(draft: Draft, source_draft_path: str | Path, track_type: str) -> int:
    """从另一个草稿导入指定类型轨道（如文本轨道），合并到当前草稿。

    - 深拷贝源草稿该类型的全部片段；
    - 每个片段的主素材 + extra_material_refs 素材都重新分配 id 并迁入当前草稿；
    - 源素材（视频/音频文件）路径保持不动（素材文件是共享的，不复制文件）；
    - 新轨道追加到当前草稿 tracks 末尾。

    返回导入的片段数量。
    """
    src = Draft.load(Path(source_draft_path))
    id_map: dict[str, str] = {}

    def _clone_mat_into(target: Draft, mat_id: str, src_idx: dict[str, dict]) -> str:
        """把源草稿里 mat_id 的素材克隆进 target，返回新 id。已克隆过直接返回。"""
        if mat_id in id_map:
            return id_map[mat_id]
        src_mat = src_idx.get(mat_id)
        if src_mat is None:
            return mat_id  # 找不到就保留原 id（悬空由 Stop Gate 报）
        new_mat = copy.deepcopy(src_mat)
        new_mat["id"] = new_id()
        kind = ""
        for k, items in (src.content.get("materials") or {}).items():
            if isinstance(items, list) and any(m.get("id") == mat_id for m in items):
                kind = k
                break
        bucket = target.content.setdefault("materials", {}).setdefault(kind or "texts", [])
        bucket.append(new_mat)
        id_map[mat_id] = new_mat["id"]
        # 素材自身可能引用别的素材（少见），不递归——文本/动画/滤镜素材自包含
        return new_mat["id"]

    src_idx = _material_refs_map(src)
    imported_segs = 0
    for st in src.tracks:
        if st.type != track_type:
            continue
        new_track: dict[str, Any] = {"id": new_id(), "type": track_type, "flag": 0,
                                     "attribute": 0, "segments": [], "is_default_name": False}
        for sraw in st.segments:
            ns = copy.deepcopy(sraw.raw)
            ns["id"] = new_id()
            # 主素材
            old_mid = sraw.material_id
            ns["material_id"] = _clone_mat_into(draft, old_mid, src_idx)
            # extra refs
            ns["extra_material_refs"] = [
                _clone_mat_into(draft, rid, src_idx)
                for rid in (sraw.raw.get("extra_material_refs") or [])
            ]
            new_track["segments"].append(ns)
            imported_segs += 1
        draft.content.setdefault("tracks", []).append(new_track)

    draft._tracks = None
    draft._mat_index = None
    return imported_segs


# ---------- 工具清单（给 describe_ops / 文档用） ----------

def describe_capcut_auto() -> list[str]:
    return [
        "duplicate_as_template(draft, segment_id) -> new_segment_id",
        "replace_text(draft, segment_id, new_text) -> bool   # 只改 text，保留 styles",
        "inspect_material(draft, segment_id) -> dict",
        "import_track(draft, source_draft_path, track_type) -> imported_count",
    ]


# ---------- 技能注册（agent 可调用，薄包装） ----------

def _register_skills() -> None:
    """把四个原语包成 draft 通道技能注册进 REGISTRY。延迟导入避免循环依赖。"""
    from .skills import Context, Skill, register

    def _dup(ctx: Context, args: dict) -> dict:
        sid = args.get("segment_id") or args.get("id")
        if not sid:
            return {"error": "缺 segment_id"}
        new_id_ = duplicate_as_template(ctx.draft, sid)
        return {"op": "duplicate_as_template", "new_segment_id": new_id_,
                "verify": {"track_exists": ["text"]}}

    def _replace(ctx: Context, args: dict) -> dict:
        sid = args.get("segment_id") or args.get("id")
        text = args.get("new_text") or args.get("text") or ""
        if not sid:
            return {"error": "缺 segment_id"}
        ok = replace_text(ctx.draft, sid, text)
        return {"op": "replace_text", "ok": ok, "text": text,
                "verify": {"text_present": [text]} if text else {}}

    def _inspect(ctx: Context, args: dict) -> dict:
        sid = args.get("segment_id") or args.get("id")
        if not sid:
            return {"error": "缺 segment_id"}
        return {"op": "inspect_material", "material": inspect_material(ctx.draft, sid)}

    def _import(ctx: Context, args: dict) -> dict:
        src = args.get("source_draft") or args.get("source_draft_path")
        ttype = args.get("track_type") or "text"
        if not src:
            return {"error": "缺 source_draft"}
        n = import_track(ctx.draft, src, ttype)
        return {"op": "import_track", "track_type": ttype, "imported": n,
                "verify": {"track_exists": [ttype]}}

    register(Skill(id="duplicate_as_template", goal="复制文本片段作模板（保留样式，清空文本）",
                   channel="draft", args={"segment_id": "源片段 id"}, required=["segment_id"],
                   sample={"segment_id": ""}, verify={"no_overlap": True},
                   fn=_dup, notes="保留样式/动画/效果，只清空 text"))
    register(Skill(id="replace_text", goal="替换文本片段文字，保留所有格式",
                   channel="draft", args={"segment_id": "片段 id", "new_text": "新文字"},
                   required=["segment_id", "new_text"], sample={"segment_id": "", "new_text": "你好"},
                   verify={"no_overlap": True},
                   fn=_replace, notes="只改 content.text，styles 数组不动"))
    register(Skill(id="inspect_material", goal="提取片段花字/字体/动画/效果 resource_id",
                   channel="draft", args={"segment_id": "片段 id"}, required=["segment_id"],
                   sample={"segment_id": ""}, verify={"no_overlap": True},
                   fn=_inspect, notes="与 resource_registry 衔接查名"))
    register(Skill(id="import_track", goal="从另一个草稿导入指定类型轨道",
                   channel="draft", args={"source_draft": "源草稿路径", "track_type": "文本/视频轨道类型"},
                   required=["source_draft"], sample={"source_draft": "", "track_type": "text"},
                   verify={"no_overlap": True},
                   fn=_import, notes="素材 id 重新分配，源文件路径不动"))


_register_skills()
