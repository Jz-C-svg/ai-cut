r"""剪映草稿 -> 统一 IR。

草稿是这台机器上唯一"结构化的剪辑真相"：`draft_content.json` 里时间线上每个片段
的起点、长度、素材来源、变速、音量、特效引用、字幕内容都在。所以主体以它为准。

时间单位约定：草稿内部用微秒（int），IR 与对外接口一律用秒（float）。
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .paths import US, PROJECT_DRAFTS

CONTENT_NAME = "draft_content.json"
META_NAME = "draft_meta_info.json"

# 轨道渲染顺序：数字小的在下层
TRACK_ORDER = {"video": 10, "audio": 20, "effect": 30, "filter": 40, "text": 50, "sticker": 60}


def to_us(sec: float | int | None) -> int | None:
    return None if sec is None else int(round(float(sec) * US))


def to_sec(us: float | int | None) -> float | None:
    return None if us is None else round(float(us) / US, 6)


def new_id() -> str:
    import uuid

    return uuid.uuid4().hex


@dataclass
class Segment:
    """时间线上的一个片段。raw 是草稿里那份原始 dict，改动直接写回它。"""

    raw: dict
    track_type: str
    material: dict | None = None

    @property
    def id(self) -> str:
        return self.raw.get("id", "")

    @property
    def material_id(self) -> str:
        return self.raw.get("material_id", "")

    @property
    def t(self) -> float:
        return to_sec(self.raw.get("target_timerange", {}).get("start", 0)) or 0.0

    @property
    def length(self) -> float:
        return to_sec(self.raw.get("target_timerange", {}).get("duration", 0)) or 0.0

    @property
    def end(self) -> float:
        return round(self.t + self.length, 6)

    @property
    def speed(self) -> float:
        return float(self.raw.get("speed") or 1.0)

    @property
    def src_in(self) -> float | None:
        st = self.raw.get("source_timerange")
        return to_sec(st.get("start")) if st else None

    @property
    def src_len(self) -> float | None:
        st = self.raw.get("source_timerange")
        return to_sec(st.get("duration")) if st else None

    @property
    def src_out(self) -> float | None:
        if self.src_in is None or self.src_len is None:
            return None
        return round(self.src_in + self.src_len, 6)

    @property
    def text(self) -> str | None:
        """字幕/文本片段的可见文字（素材 content 是 JSON 字符串）。"""
        mat = self.material
        if not mat or "content" not in mat:
            return None
        raw = mat.get("content")
        if isinstance(raw, str):
            try:
                return json.loads(raw).get("text")
            except (ValueError, AttributeError):
                return raw
        return None

    def material_name(self) -> str:
        mat = self.material or {}
        return str(mat.get("material_name") or mat.get("name") or "")

    def material_path(self) -> str:
        mat = self.material or {}
        return str(mat.get("path") or "")

    def label(self) -> str:
        if self.track_type == "text":
            return (self.text or "")[:24]
        return self.material_name() or self.material_id[:8]

    def ir(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "t": self.t,
            "len": self.length,
            "end": self.end,
        }
        if self.src_in is not None:
            out["src_in"] = self.src_in
            out["src_len"] = self.src_len
        if self.speed != 1.0:
            out["speed"] = self.speed
        if self.track_type == "text":
            out["text"] = self.text
        else:
            out["material"] = self.material_name()
        refs = self.raw.get("extra_material_refs") or []
        if refs:
            out["refs"] = list(refs)
        return out


@dataclass
class Track:
    raw: dict
    segments: list[Segment] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.raw.get("id", "")

    @property
    def type(self) -> str:
        return self.raw.get("type", "")


@dataclass
class Draft:
    """一个剪映草稿目录（或直接指向 draft_content.json）。"""

    path: Path
    content: dict
    meta: dict | None = None
    _tracks: list[Track] | None = None
    _mat_index: dict[str, tuple[str, dict]] | None = None

    # ---------- 载入 / 保存 ----------

    @classmethod
    def load(cls, target: str | Path) -> "Draft":
        p = Path(target)
        if p.is_dir():
            content_path = p / CONTENT_NAME
            meta_path = p / META_NAME
        else:
            content_path = p
            meta_path = p.with_name(META_NAME)
        if not content_path.exists():
            raise FileNotFoundError(f"找不到草稿文件：{content_path}")

        # v4.0.0 容错：主文件 JSON 解析失败时，尝试从 .tmp 残留恢复
        # （.tmp 是已 fsync 的完整写入，只是没来得及原子改名就被中断）。
        content: dict | None = None
        try:
            content = json.loads(content_path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError) as exc_main:
            from .resilience import find_tmp_candidates  # 局部导入避免循环依赖
            recovered = False
            for tmp in find_tmp_candidates(content_path):
                try:
                    candidate = json.loads(tmp.read_text(encoding="utf-8-sig"))
                except (ValueError, OSError):
                    continue
                # 恢复成功：用完整的 tmp 覆盖损坏的主文件，并清理临时文件
                content = candidate
                recovered = True
                try:
                    from .resilience import atomic_write_json  # 局部导入避免循环依赖
                    atomic_write_json(content_path, candidate, ensure_ascii=False)
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                break
            if not recovered:
                raise RuntimeError(
                    f"草稿主文件已损坏且无可用临时恢复文件：{content_path}（{type(exc_main).__name__}: {exc_main}）"
                ) from exc_main

        meta = None
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
            except ValueError:
                meta = None
        return cls(path=content_path, content=content, meta=meta)

    @classmethod
    def from_name(cls, name: str, root: Path | None = None) -> "Draft":
        base = Path(root) if root else PROJECT_DRAFTS
        return cls.load(base / name)

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def name(self) -> str:
        return self.content.get("name") or self.dir.name

    def save(self, target: str | Path | None = None) -> Path:
        from .resilience import atomic_write_json  # 局部导入避免循环依赖
        out = Path(target) if target else self.path
        self.content["update_time"] = int(time.time() * US)
        # v4.0.0 原子写入：tmp + fsync + replace，中断不留半文件
        return atomic_write_json(out, self.content, ensure_ascii=False)

    def clone(self) -> "Draft":
        return Draft(path=self.path, content=copy.deepcopy(self.content), meta=copy.deepcopy(self.meta))

    def reload(self) -> "Draft":
        self.content = json.loads(self.path.read_text(encoding="utf-8-sig"))
        self._tracks = None
        self._mat_index = None
        return self

    # ---------- 素材索引 ----------

    def _index(self) -> dict[str, tuple[str, dict]]:
        if self._mat_index is None:
            idx: dict[str, tuple[str, dict]] = {}
            mats = self.content.get("materials") or {}
            for kind, items in mats.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and item.get("id"):
                        idx.setdefault(item["id"], (kind, item))
            self._mat_index = idx
        return self._mat_index

    def material(self, material_id: str) -> dict | None:
        hit = self._index().get(material_id)
        return hit[1] if hit else None

    def material_kind(self, material_id: str) -> str:
        hit = self._index().get(material_id)
        return hit[0] if hit else ""

    def materials_of(self, kind: str) -> list[dict]:
        return list((self.content.get("materials") or {}).get(kind) or [])

    def text_of(self, material_id: str) -> str | None:
        mat = self.material(material_id)
        if not mat or not isinstance(mat.get("content"), str):
            return None
        try:
            return json.loads(mat["content"]).get("text")
        except ValueError:
            return None

    # ---------- 轨道与片段 ----------

    def _build_tracks(self) -> list[Track]:
        tracks: list[Track] = []
        for raw_track in self.content.get("tracks") or []:
            ttype = raw_track.get("type", "")
            segs: list[Segment] = []
            for sraw in raw_track.get("segments") or []:
                mat = self.material(sraw.get("material_id", ""))
                segs.append(Segment(raw=sraw, track_type=ttype, material=mat))
            segs.sort(key=lambda s: s.t)
            tracks.append(Track(raw=raw_track, segments=segs))
        order = TRACK_ORDER
        tracks.sort(key=lambda t: (order.get(t.type, 99), t.raw.get("flag", 0)))
        return tracks

    @property
    def tracks(self) -> list[Track]:
        if self._tracks is None:
            self._tracks = self._build_tracks()
        return self._tracks

    @property
    def track_types(self) -> list[str]:
        return [t.type for t in self.tracks]

    def segments_of(self, track_type: str) -> list[Segment]:
        return [s for t in self.tracks if t.type == track_type for s in t.segments]

    @property
    def video_segments(self) -> list[Segment]:
        return self.segments_of("video")

    @property
    def text_segments(self) -> list[Segment]:
        return self.segments_of("text")

    @property
    def audio_segments(self) -> list[Segment]:
        return self.segments_of("audio")

    def all_segments(self) -> Iterator[Segment]:
        for t in self.tracks:
            for s in t.segments:
                yield s

    # ---------- 读数 ----------

    @property
    def declared_duration(self) -> float:
        return to_sec(self.content.get("duration")) or 0.0

    @property
    def max_end(self) -> float:
        return round(max((s.end for s in self.all_segments()), default=0.0), 6)

    @property
    def fps(self) -> int:
        return int(self.content.get("fps") or 30)

    def ir(self) -> dict:
        """归一化 IR：给 diff、验证、模型看的那份结构。"""
        return {
            "name": self.name,
            "path": str(self.dir),
            "duration": self.declared_duration,
            "max_end": self.max_end,
            "fps": self.fps,
            "tracks": [
                {
                    "type": t.type,
                    "id": t.id,
                    "segments": [s.ir() for s in t.segments],
                }
                for t in self.tracks
            ],
        }

    def describe(self) -> list[str]:
        """给人/给模型看的一行行情报。"""
        lines = [f"草稿 {self.name} · 时长 {self.declared_duration:.2f}s · {self.fps}fps · 轨道 {len(self.tracks)} 条"]
        for t in self.tracks:
            lines.append(f"  [{t.type}] {len(t.segments)} 段")
            for s in t.segments:
                head = f"    {s.t:6.2f} -> {s.end:6.2f} ({s.length:5.2f}s) {s.label()}"
                if s.track_type != "text" and s.src_in is not None:
                    head += f" | 源 {s.src_in:.2f}+{s.src_len:.2f}s"
                if s.speed != 1.0:
                    head += f" | {s.speed}x"
                lines.append(head)
        return lines
