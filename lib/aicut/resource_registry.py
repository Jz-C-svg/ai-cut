r"""v3.1 资源 ID 注册表（resourceId registry）。

设计原则（最高优先级）：
  - 只收录**可核实来源**的 resourceId，禁止编造。
  - 每条 entry 必须带 ``source``，能追溯到 external/refs 源码、真实草稿或 JianYingDraft 元数据。
  - 这是**可验证样例集，不是全量库**——缺什么类别就如实留空并说明。

数据落盘：``config/resource_registry.json``（JSON，可版本管理）。

条目结构::

    {
      "id": "fonts:6841117833447870990",     # 稳定主键 = category:resource_id
      "name": "3D 만화",                       # 资源名（可能为空字符串）
      "category": "fonts",                     # 见 CATEGORIES / SUPPLEMENTARY
      "resource_id": "6841117833447870990",
      "effect_id": "20402387",
      "source": ["jianyingdraft_metadata:font_meta.py", ...],  # 多来源合并
      "verified_at": "2026-09-11",
      "notes": ""
    }
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Iterable

try:  # 项目内运行
    from . import paths
except Exception:  # 直接脚本运行时
    import paths  # type: ignore

# ---- 类别定义 ---------------------------------------------------------------
# 任务要求的 7 个主类别（对外查询的主入口）。
REQUIRED_CATEGORIES = [
    "text_styles",    # 花字
    "text_templates", # 文本模板
    "stickers",       # 贴纸
    "transitions",    # 转场
    "effects",        # 特效（视频场景/人物特效）
    "filters",        # 滤镜
    "fonts",          # 字体
]

# 真实源码里确实存在、但不属于上述 7 桶的补充类别（不丢弃真实数据）。
SUPPLEMENTARY_CATEGORIES = [
    "text_intro",       # 文本入场动画
    "text_outro",       # 文本出场动画
    "text_loop",        # 文本组合/循环动画
    "video_intro",      # 视频入场动画
    "video_outro",      # 视频出场动画
    "video_group_anim", # 视频组合动画
    "audio_scene",      # 音频场景特效
    "audio_tone",       # 人声/音色特效
    "speech_song",      # 语音成曲
    "mix_modes",        # 混合模式
    "masks",            # 蒙版
]

ALL_CATEGORIES = REQUIRED_CATEGORIES + SUPPLEMENTARY_CATEGORIES

# 缺口说明：duo-vite/duoec 等对外宣称的全量规模 vs 本注册表可核实收录规模。
SCALE_DISCLOSURE = {
    "claimed": {
        "text_styles(花字)": "2000+",
        "text_templates(文本模板)": "8000+",
        "stickers(贴纸)": "7805",
        "transitions(转场)": "700+",
    },
    "why_gap": (
        "外部参考源码(capcut-mate / capcut-mcp / catalogue.ts)只覆盖 字体/滤镜/转场/"
        "视频特效/动画/混音模式/蒙版；花字、文本模板、贴纸需要剪映专业版内部数据库或 "
        "duoec.com 付费资料，参考源码未包含。真实草稿目录里这些素材几乎为空，"
        "因此本注册表对应类别只能如实留空。"
    ),
    "scope_note": "这是可验证样例集，不是全量库。每个 ID 均可追溯到 external/refs 源码或真实草稿。",
}

REQUIRED_FIELDS = ("id", "name", "category", "resource_id", "effect_id",
                   "source", "verified_at", "notes")


def _today() -> str:
    return _dt.date.today().isoformat()


def default_registry_path() -> Path:
    return paths.CONFIG_DIR / "resource_registry.json"


class ResourceRegistry:
    """按类别查询的 resourceId 注册表。

    用法::

        reg = ResourceRegistry.load()
        hits = reg.query("fonts", keyword="明朝")
        one  = reg.get_by_resource_id("6841117833447870990")
        reg.add_resource({...})
        reg.save()
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_registry_path()
        self.entries: dict[str, dict] = {}   # id -> entry

    # ---- 持久化 -----------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None = None) -> "ResourceRegistry":
        reg = cls(path)
        if reg.path.exists():
            data = json.loads(reg.path.read_text(encoding="utf-8-sig"))
            for e in data.get("entries", []):
                reg._ingest(e, merge=False)
        return reg

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "3.1",
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "scope_note": SCALE_DISCLOSURE["scope_note"],
            "disclosure": SCALE_DISCLOSURE,
            "entries": [self.entries[k] for k in sorted(self.entries)],
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        return self.path

    # ---- 写入 -------------------------------------------------------------
    @staticmethod
    def make_id(category: str, resource_id: str) -> str:
        return f"{category}:{resource_id}"

    def add_resource(self, entry: dict) -> dict:
        """新增或合并一条资源。缺少 source 会拒绝（防止无来源条目入库）。"""
        missing = [f for f in REQUIRED_FIELDS if f not in entry]
        if missing:
            raise ValueError(f"条目缺少字段: {missing} -> {entry!r}")
        if entry["category"] not in ALL_CATEGORIES:
            raise ValueError(f"未知类别 {entry['category']!r}")
        if not entry.get("source"):
            raise ValueError(f"条目缺少来源(source)，禁止无来源入库: {entry!r}")
        rid = str(entry["resource_id"])
        if not rid or rid in ("None", "nan"):
            raise ValueError(f"条目 resource_id 为空，疑似编造: {entry!r}")
        entry = dict(entry)
        entry.setdefault("effect_id", "")
        entry.setdefault("name", "")
        entry.setdefault("notes", "")
        entry.setdefault("verified_at", _today())
        src = entry["source"]
        if isinstance(src, str):
            src = [src]
        entry["source"] = list(src)
        eid = entry["id"]
        if eid in self.entries:  # 合并来源，不覆盖已有信息
            old = self.entries[eid]
            merged = set(old.get("source", [])) | set(entry["source"])
            old["source"] = sorted(merged)
            if not old.get("name") and entry.get("name"):
                old["name"] = entry["name"]
            return old
        self.entries[eid] = entry
        return entry

    def _ingest(self, entry: dict, merge: bool = True) -> None:
        """从磁盘读入时宽松一点：补齐缺省字段。"""
        entry = dict(entry)
        for f in REQUIRED_FIELDS:
            entry.setdefault(f, "" if f != "source" else [])
        if isinstance(entry["source"], str):
            entry["source"] = [entry["source"]]
        self.entries[entry["id"]] = entry

    # ---- 查询 -------------------------------------------------------------
    def query(self, category: str | None = None,
              keyword: str | None = None) -> list[dict]:
        out: list[dict] = []
        for e in self.entries.values():
            if category and e["category"] != category:
                continue
            if keyword:
                kw = keyword.lower()
                hay = f"{e.get('name','')} {e.get('resource_id','')} {e.get('effect_id','')}".lower()
                if kw not in hay:
                    continue
            out.append(e)
        out.sort(key=lambda x: (x["category"], x["name"]))
        return out

    def get_by_resource_id(self, rid: str) -> dict | None:
        rid = str(rid)
        for e in self.entries.values():
            if str(e.get("resource_id")) == rid:
                return e
        return None

    # ---- 统计 -------------------------------------------------------------
    def stats(self) -> dict:
        by_cat: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for e in self.entries.values():
            by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
            for s in e.get("source", []):
                key = s.split(":", 1)[0]
                by_source[key] = by_source.get(key, 0) + 1
        required_present = {c: by_cat.get(c, 0) for c in REQUIRED_CATEGORIES}
        return {
            "total": len(self.entries),
            "by_category": dict(sorted(by_cat.items())),
            "required_categories": required_present,
            "by_source": dict(sorted(by_source.items())),
            "all_categories": ALL_CATEGORIES,
        }

    # ---- 从 JianYingDraft 元数据同步 -------------------------------------
    def sync_from_jianyingdraft(self, lib_root: str | Path | None = None) -> dict:
        """自动从项目 lib/JianYingDraft/metadata 的枚举同步 resource_id。

        source 标注为 ``jianyingdraft_metadata:<file>``。
        """
        import importlib
        import sys

        root = Path(lib_root) if lib_root else paths.LIB
        root = root.resolve()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        meta = importlib.import_module("JianYingDraft.metadata")
        # (枚举类名, 目标类别, 元数据文件名)
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
        added = 0
        for cls_name, category, file in mapping:
            cls = getattr(meta, cls_name, None)
            if cls is None:
                continue
            src = f"jianyingdraft_metadata:{file}"
            for member in cls:
                v = member.value
                rid = str(getattr(v, "resource_id", "") or "")
                if not rid:
                    continue
                name = getattr(v, "name", None) or getattr(v, "title", "") or member.name
                entry = {
                    "id": self.make_id(category, rid),
                    "name": str(name),
                    "category": category,
                    "resource_id": rid,
                    "effect_id": str(getattr(v, "effect_id", "") or ""),
                    "source": [src],
                    "verified_at": _today(),
                    "notes": f"枚举 {cls_name}",
                }
                self.add_resource(entry)
                added += 1
        return {"added": added, "total_after": len(self.entries)}


def entries_from_enum_module(meta_module, mapping: Iterable[tuple],
                             source_prefix: str) -> list[dict]:
    """通用：从一个 metadata 模块（任意来源）按 mapping 产出 entries。

    mapping 元素为 ``(枚举类名, 目标类别, 元数据文件名)``。
    """
    out: list[dict] = []
    for cls_name, category, file in mapping:
        cls = getattr(meta_module, cls_name, None)
        if cls is None:
            continue
        src = f"{source_prefix}:{file}"
        for member in cls:
            v = member.value
            rid = str(getattr(v, "resource_id", "") or "")
            if not rid:
                continue
            name = getattr(v, "name", None) or getattr(v, "title", "") or member.name
            out.append({
                "id": f"{category}:{rid}",
                "name": str(name),
                "category": category,
                "resource_id": rid,
                "effect_id": str(getattr(v, "effect_id", "") or ""),
                "source": [src],
                "verified_at": _today(),
                "notes": f"枚举 {cls_name}",
            })
    return out
