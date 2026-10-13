r"""工程路径与配置。

所有模块都从这里取路径，不各自拼字符串，避免"一半相对路径一半绝对路径"的坑。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

US = 1_000_000  # 剪映草稿里的时间单位是微秒


def _find_root(start: Path) -> Path:
    for p in (start, *start.parents):
        if (p / "config" / "aicut.json").exists():
            return p
    return start.parents[1]


ROOT = _find_root(Path(__file__).resolve())

SCRIPTS = ROOT / "scripts"
LIB = ROOT / "lib"
WORK = ROOT / "work"
LOGS = ROOT / "09-logs"
CONFIG_DIR = ROOT / "config"
CONTENT_LIBRARY = ROOT / "content-library"
ASSETS = ROOT / "04-assets"
OUTPUT = ROOT / "07-output"
PROJECT_DRAFTS = ROOT / "06-drafts"
DATA = ROOT / "08-data"
REPORTS = ROOT / "reports"
TRANSCRIPTS = ROOT / "02-transcript"
SHEETS = ROOT / "03-scripts"


@lru_cache(maxsize=1)
def config() -> dict:
    path = CONFIG_DIR / "aicut.json"
    raw: dict = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:  # v4.0.0：配置文件损坏不崩溃，按空配置+警告处理
            print(f"[配置警告] aicut.json 解析失败（{exc}），已按空配置补全默认值", file=sys.stderr)
            raw = {}
    # v4.0.0：校验并补全缺失字段（局部导入避免与 resilience 循环依赖）
    from .resilience import validate_config
    completed, warnings = validate_config(raw)
    for w in warnings:
        print(f"[配置警告] {w}", file=sys.stderr)
    return completed


def draft_root() -> Path:
    """剪映自己的草稿目录（发布草稿的目的地）。"""
    configured = config().get("draft_root")
    if configured:
        return Path(configured)
    return Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def python_exe() -> str:
    return config().get("python") or sys.executable


def ffmpeg() -> str:
    return config().get("ffmpeg", "ffmpeg")


def ffprobe() -> str:
    return config().get("ffprobe", "ffprobe")


def memory_path() -> Path:
    return DATA / "memory.sqlite"


def snapshots_dir() -> Path:
    d = WORK / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_dir(path: Path) -> Path:
    Path(path).mkdir(parents=True, exist_ok=True)
    return Path(path)


def fingerprint(path: str | Path) -> str:
    """素材/草稿指纹：路径 + 大小 + 修改时间。用于缓存失效判断。"""
    p = Path(path)
    try:
        st = p.stat()
        raw = f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = f"{p}|missing"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def sha1_file(path: str | Path, limit: int | None = None) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20 if limit is None else limit)
            if not chunk:
                break
            h.update(chunk)
            if limit is not None:
                break
    return h.hexdigest()
