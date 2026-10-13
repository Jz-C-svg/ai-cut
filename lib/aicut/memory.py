r"""memory.sqlite · 唯一的记忆库。

存七类东西（对应设计里的"七张表"）：
  episodes      每次任务/每一步的感知-决策-动作-结果
  skills        可复用操作序列 + 成功率 + 来源
  preferences   从演示或数据里学到的偏好规则
  visual_events 素材视觉事件流缓存（按素材指纹）
  snapshots     草稿快照索引（一键回滚）
  facts         本机事实（窗口类名、快捷键、草稿路径规则）
  style_params  风格参数（可被后台数据回灌）

config/*.json 保留为人可读视图，这里做真相源。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .paths import memory_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT, step INTEGER, kind TEXT, goal TEXT, detail TEXT,
    evidence TEXT, ok INTEGER, created REAL
);
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY, goal TEXT, channel TEXT, preconditions TEXT,
    steps TEXT, verify TEXT, success REAL, runs INTEGER, fails INTEGER,
    source TEXT, source_url TEXT, updated REAL
);
CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT, rule TEXT UNIQUE, evidence TEXT,
    confidence REAL, source TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS visual_events (
    fingerprint TEXT PRIMARY KEY, material TEXT, payload TEXT, updated REAL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY, draft TEXT, path TEXT, note TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS facts (
    key TEXT PRIMARY KEY, value TEXT, source TEXT, updated REAL
);
CREATE TABLE IF NOT EXISTS style_params (
    style TEXT PRIMARY KEY, params TEXT, note TEXT, updated REAL
);
CREATE INDEX IF NOT EXISTS idx_episodes_task ON episodes(task);
CREATE INDEX IF NOT EXISTS idx_snapshots_draft ON snapshots(draft);
"""


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _u(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


class Memory:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else memory_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- episodes ----------

    def add_episode(self, *, task: str, kind: str, step: int = 0, goal: str = "", detail: Any = None,
                    evidence: str = "", ok: bool | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO episodes(task, step, kind, goal, detail, evidence, ok, created)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (task, step, kind, goal, _j(detail) if detail is not None else None, evidence,
             None if ok is None else int(ok), time.time()),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def episodes(self, task: str | None = None, limit: int = 30) -> list[dict]:
        sql = "SELECT * FROM episodes"
        args: tuple = ()
        if task:
            sql += " WHERE task=?"
            args = (task,)
        sql += " ORDER BY id DESC LIMIT ?"
        rows = self.db.execute(sql, (*args, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = _u(d.get("detail"))
            out.append(d)
        return out

    def tasks(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT task, COUNT(*) n, MIN(created) started, MAX(created) ended,"
            " SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) fails"
            " FROM episodes GROUP BY task ORDER BY MAX(id) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- skills ----------

    def upsert_skill(self, skill: dict) -> None:
        sid = skill["id"]
        row = self.db.execute("SELECT success, runs, fails FROM skills WHERE id=?", (sid,)).fetchone()
        runs = int(row["runs"]) if row else 0
        fails = int(row["fails"]) if row else 0
        success = float(row["success"]) if row else 0.0
        if row is None:
            runs, fails = int(skill.get("runs", 0)), int(skill.get("fails", 0))
            success = float(skill.get("success", 1.0 if runs else 0.0))
        self.db.execute(
            "INSERT INTO skills(id, goal, channel, preconditions, steps, verify, success, runs, fails,"
            " source, source_url, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET goal=excluded.goal, channel=excluded.channel,"
            " preconditions=excluded.preconditions, steps=excluded.steps, verify=excluded.verify,"
            " source=excluded.source, source_url=excluded.source_url, updated=excluded.updated",
            (
                sid,
                skill.get("goal", ""),
                skill.get("channel", "draft"),
                _j(skill.get("preconditions") or skill.get("pre") or []),
                _j(skill.get("steps") or []),
                _j(skill.get("verify") or {}),
                success,
                runs,
                fails,
                skill.get("source", "builtin"),
                skill.get("source_url", ""),
                time.time(),
            ),
        )
        self.db.commit()

    def record_run(self, skill_id: str, ok: bool) -> dict:
        row = self.db.execute("SELECT runs, fails FROM skills WHERE id=?", (skill_id,)).fetchone()
        if row is None:
            return {}
        runs = int(row["runs"]) + 1
        fails = int(row["fails"]) + (0 if ok else 1)
        success = (runs - fails) / runs if runs else 0.0
        self.db.execute(
            "UPDATE skills SET runs=?, fails=?, success=?, updated=? WHERE id=?",
            (runs, fails, round(success, 4), time.time(), skill_id),
        )
        self.db.commit()
        return {"id": skill_id, "runs": runs, "fails": fails, "success": round(success, 4)}

    def skills(self, order: str = "success DESC") -> list[dict]:
        rows = self.db.execute(f"SELECT * FROM skills ORDER BY {order}").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["steps"] = _u(d["steps"]) or []
            d["verify"] = _u(d["verify"]) or {}
            d["preconditions"] = _u(d["preconditions"]) or []
            out.append(d)
        return out

    def get_skill(self, skill_id: str) -> dict | None:
        for s in self.skills():
            if s["id"] == skill_id:
                return s
        return None

    # ---------- preferences ----------

    def add_preference(self, rule: str, *, evidence: str = "", confidence: float = 0.6,
                       source: str = "demo") -> None:
        self.db.execute(
            "INSERT INTO preferences(rule, evidence, confidence, source, created) VALUES(?,?,?,?,?)"
            " ON CONFLICT(rule) DO UPDATE SET evidence=excluded.evidence,"
            " confidence=MAX(preferences.confidence, excluded.confidence), source=excluded.source",
            (rule.strip(), evidence, float(confidence), source, time.time()),
        )
        self.db.commit()

    def preferences(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM preferences ORDER BY confidence DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- visual events ----------

    def cache_visual(self, fingerprint: str, material: str, payload: Any) -> None:
        self.db.execute(
            "INSERT INTO visual_events(fingerprint, material, payload, updated) VALUES(?,?,?,?)"
            " ON CONFLICT(fingerprint) DO UPDATE SET payload=excluded.payload, updated=excluded.updated",
            (fingerprint, material, _j(payload), time.time()),
        )
        self.db.commit()

    def get_visual(self, fingerprint: str) -> Any:
        row = self.db.execute(
            "SELECT payload FROM visual_events WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        return _u(row["payload"]) if row else None

    # ---------- snapshots ----------

    def add_snapshot(self, snapshot_id: str, draft: str, path: str, note: str = "") -> None:
        self.db.execute(
            "INSERT INTO snapshots(id, draft, path, note, created) VALUES(?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET path=excluded.path, note=excluded.note",
            (snapshot_id, draft, path, note, time.time()),
        )
        self.db.commit()

    def snapshots(self, draft: str | None = None, limit: int = 30) -> list[dict]:
        if draft:
            rows = self.db.execute(
                "SELECT * FROM snapshots WHERE draft=? ORDER BY created DESC LIMIT ?", (draft, limit)
            ).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM snapshots ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        return dict(row) if row else None

    # ---------- facts ----------

    def set_fact(self, key: str, value: Any, source: str = "local") -> None:
        self.db.execute(
            "INSERT INTO facts(key, value, source, updated) VALUES(?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source, updated=excluded.updated",
            (key, _j(value), source, time.time()),
        )
        self.db.commit()

    def fact(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        return _u(row["value"]) if row else default

    def facts(self) -> dict:
        rows = self.db.execute("SELECT key, value, source FROM facts").fetchall()
        return {r["key"]: _u(r["value"]) for r in rows}

    # ---------- style params ----------

    def set_style(self, style: str, params: dict, note: str = "") -> None:
        self.db.execute(
            "INSERT INTO style_params(style, params, note, updated) VALUES(?,?,?,?)"
            " ON CONFLICT(style) DO UPDATE SET params=excluded.params, note=excluded.note, updated=excluded.updated",
            (style, _j(params), note, time.time()),
        )
        self.db.commit()

    def styles(self) -> dict:
        rows = self.db.execute("SELECT style, params, note FROM style_params").fetchall()
        return {r["style"]: {"params": _u(r["params"]), "note": r["note"]} for r in rows}

    # ---------- 概览 ----------

    def stats(self) -> dict:
        out = {}
        for table in ("episodes", "skills", "preferences", "visual_events", "snapshots", "facts", "style_params"):
            out[table] = self.db.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        out["db"] = str(self.path)
        return out


_default: Memory | None = None


def open_memory(path: str | Path | None = None) -> Memory:
    """进程内共享的默认记忆库。"""
    global _default
    if path is not None:
        return Memory(path)
    if _default is None:
        _default = Memory()
    return _default
