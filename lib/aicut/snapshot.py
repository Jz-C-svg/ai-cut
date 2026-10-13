r"""草稿快照与回滚。

剪映没有"保存"快捷键，编辑靠自动保存；所以主体在动大手术前必须先留后路。
快照存在 work/snapshots/<草稿名>/<时间戳>/，索引写进 memory.sqlite。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .draft import CONTENT_NAME, META_NAME, Draft
from .memory import Memory, open_memory
from .paths import snapshots_dir


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"


def snapshot(target: str | Path, note: str = "", memory: Memory | None = None) -> dict:
    draft = Draft.load(target)
    sid = _stamp()
    dest = snapshots_dir() / draft.name / sid
    dest.mkdir(parents=True, exist_ok=True)
    for name in (CONTENT_NAME, META_NAME):
        src = draft.dir / name
        if src.exists():
            shutil.copy2(src, dest / name)
    info = {
        "id": sid,
        "draft": draft.name,
        "note": note,
        "created": time.time(),
        "duration": draft.declared_duration,
        "draft_dir": str(draft.dir),
        "path": str(dest),
    }
    (dest / "snapshot.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    (memory or open_memory()).add_snapshot(sid, draft.name, str(dest), note)
    return info


def snapshots(draft: str | None = None, memory: Memory | None = None) -> list[dict]:
    return (memory or open_memory()).snapshots(draft)


def _resolve(snapshot_id: str, memory: Memory | None = None) -> dict:
    mem = memory or open_memory()
    found = mem.get_snapshot(snapshot_id)
    if found:
        path = Path(found["path"])
    else:
        matches = list(snapshots_dir().glob(f"*/{snapshot_id}"))
        if not matches:
            raise FileNotFoundError(f"找不到快照：{snapshot_id}")
        path = matches[0]
    info_path = path / "snapshot.json"
    info = json.loads(info_path.read_text(encoding="utf-8-sig")) if info_path.exists() else {}
    info.setdefault("id", snapshot_id)
    info.setdefault("draft_dir", "")
    info["path"] = str(path)
    return info


def restore(snapshot_id: str, *, backup: bool = True, memory: Memory | None = None) -> dict:
    mem = memory or open_memory()
    info = _resolve(snapshot_id, mem)
    dest_dir = Path(info.get("draft_dir") or "")
    if not dest_dir or not dest_dir.exists():
        raise FileNotFoundError(f"快照 {snapshot_id} 里没有可用的草稿目录")
    if backup:
        snapshot(dest_dir, note=f"回滚到 {snapshot_id} 之前自动备份", memory=mem)
    copied = []
    for name in (CONTENT_NAME, META_NAME):
        src = Path(info["path"]) / name
        if src.exists():
            shutil.copy2(src, dest_dir / name)
            copied.append(name)
    mem.add_snapshot(snapshot_id, dest_dir.name, info["path"], f"{info.get('note','')} | 已回滚")
    after = Draft.load(dest_dir)
    return {
        "snapshot": snapshot_id,
        "draft": dest_dir.name,
        "copied": copied,
        "duration": after.declared_duration,
        "segments": len(list(after.all_segments())),
    }


def prune(draft: str, keep: int = 20, memory: Memory | None = None) -> list[str]:
    """只保留最近 keep 个快照，避免 work 目录无限膨胀。"""
    mem = memory or open_memory()
    rows = mem.snapshots(draft, limit=1000)
    removed = []
    for row in rows[keep:]:
        path = Path(row["path"])
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        removed.append(row["id"])
    return removed
