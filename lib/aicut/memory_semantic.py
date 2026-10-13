r"""memory_semantic · 语义记忆检索层（向量 + 关键词降级）。

给 knowledge_brief / 案例库提供"按语义相关性召回"能力：
- embedding 首选 Ollama（/api/embeddings，nomic-embed-text，本地免费）
- Ollama 不可用时自动降级为字符 n-gram hash 向量（纯 numpy，中文可用，无需模型）
- 向量与文本存 08-data/memory.sqlite 的 vec_items 表，与既有记忆同一库

用法:
    from aicut import memory_semantic as ms
    ms.upsert("case:xxx", "文本内容...", kind="case")
    hits = ms.search("剪掉停顿 压到 20 秒", kind="case", top_k=5)
"""
from __future__ import annotations

import hashlib
import json
import struct
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "08-data" / "memory.sqlite"

from .ollama_endpoint import ollama_url
OLLAMA_URL = ollama_url('/api/embeddings')
OLLAMA_MODEL = "nomic-embed-text"
_DIM = 256  # hash 向量维度


# ---------- sqlite ----------

def _db():
    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS vec_items (
        key TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL,
        vec BLOB, embedder TEXT, ts REAL)""")
    return conn


def _pack(v: np.ndarray) -> bytes:
    return struct.pack(f"{len(v)}f", *v.astype(np.float32))


def _unpack(b: bytes) -> np.ndarray:
    return np.array(struct.unpack(f"{len(b)//4}f", b), dtype=np.float32)


# ---------- embedding ----------

_ollama_ok: bool | None = None  # 一次性探测缓存：进程内只试一次


def _ollama_embed(text: str) -> list[float] | None:
    global _ollama_ok
    if _ollama_ok is False:
        return None
    try:
        payload = json.dumps({"model": OLLAMA_MODEL, "prompt": text[:4000]}).encode("utf-8")
        req = urllib.request.Request(OLLAMA_URL, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        emb = data.get("embedding")
        if emb:
            _ollama_ok = True
            return emb
        _ollama_ok = False
        return None
    except Exception:
        _ollama_ok = False
        return None


def _hash_embed(text: str) -> np.ndarray:
    """字符 3-gram hash 向量：零依赖的中文可用降级方案。"""
    v = np.zeros(_DIM, dtype=np.float32)
    t = "".join(text.split())
    grams = {t[i:i + 3] for i in range(max(len(t) - 2, 1))}
    for g in grams:
        h = int.from_bytes(hashlib.md5(g.encode("utf-8")).digest()[:4], "little")
        v[h % _DIM] += 1.0
    n = float(np.linalg.norm(v))
    return v / n if n else v


def embed(text: str) -> tuple[np.ndarray, str]:
    """返回 (向量, embedder名)。Ollama 可用用真语义向量，否则 hash 降级。"""
    e = _ollama_embed(text)
    if e:
        return np.array(e, dtype=np.float32), f"ollama:{OLLAMA_MODEL}"
    return _hash_embed(text), "hash3gram"


# ---------- 增查 ----------

def upsert(key: str, text: str, kind: str = "note", meta: dict | None = None) -> bool:
    """写入/更新一条语义条目。meta 会以 JSON 存进 text 前缀之外的单列（放 text 尾部 JSON 行）。"""
    vec, embedder = embed(text)
    payload = json.dumps({"meta": meta or {}, "text": text}, ensure_ascii=False)
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO vec_items(key, kind, text, vec, embedder, ts) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, text=excluded.text, "
            "vec=excluded.vec, embedder=excluded.embedder, ts=excluded.ts",
            (key, kind, payload, _pack(vec), embedder, time.time()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete(key: str) -> None:
    conn = _db()
    try:
        conn.execute("DELETE FROM vec_items WHERE key=?", (key,))
        conn.commit()
    finally:
        conn.close()


def search(query: str, kind: str | None = None, top_k: int = 5) -> list[dict]:
    """语义检索。返回 [{key, kind, text, meta, score}]，按相似度降序。

    查询向量与库内向量必须同 embedder 才可比：混库时对两类向量分别归一化
    不可行（维度不同），所以查询时先取与查询同 embedder 的条目参与余弦，
    其余条目走关键词重叠分兜底，保证任何情况下都有结果。
    """
    qvec, qemb = embed(query)
    conn = _db()
    try:
        rows = conn.execute("SELECT key, kind, text, vec, embedder FROM vec_items").fetchall()
    finally:
        conn.close()
    q_tokens = set("".join(query.split()))
    out: list[dict] = []
    for key, kind_, text, vec, emb in rows:
        if kind and kind_ != kind:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"text": text, "meta": {}}
        body = str(payload.get("text", ""))
        score = 0.0
        if vec and emb == qemb:
            v = _unpack(vec)
            nv, nq = float(np.linalg.norm(v)), float(np.linalg.norm(qvec))
            score = float(v @ qvec / (nv * nq)) if nv and nq else 0.0
        else:
            # 降级：字符重叠 Jaccard 兜底
            t_tokens = set("".join(body.split()))
            if t_tokens:
                score = len(q_tokens & t_tokens) / max(len(q_tokens | t_tokens), 1) * 0.6
        out.append({"key": key, "kind": kind_, "text": body,
                    "meta": payload.get("meta") or {}, "score": round(score, 4)})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:top_k]


def count(kind: str | None = None) -> int:
    conn = _db()
    try:
        if kind:
            return int(conn.execute("SELECT COUNT(*) FROM vec_items WHERE kind=?", (kind,)).fetchone()[0])
        return int(conn.execute("SELECT COUNT(*) FROM vec_items").fetchone()[0])
    finally:
        conn.close()
