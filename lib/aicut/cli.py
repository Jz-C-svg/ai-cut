r"""CLI 公共件：统一 stdout 编码、草稿定位、输出格式。

所有 v2 脚本共用一个入口头部：

    ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT / "lib"))
    from aicut import cli
    cli.setup()
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from .paths import PROJECT_DRAFTS, draft_root


def setup() -> None:
    """Windows 控制台默认不是 utf-8，中文会变问号。"""
    enc = str(getattr(sys.stdout, "encoding", "")).lower()
    if enc not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    err = str(getattr(sys.stderr, "encoding", "")).lower()
    if err not in ("utf-8", "utf8"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def resolve_draft(arg: str, *, extra_roots: list[Path] | None = None) -> Path:
    """把「草稿名」或「路径」统一成一个真实存在的草稿目录。"""
    p = Path(arg)
    if p.exists():
        return p if p.is_dir() else p.parent
    roots = [PROJECT_DRAFTS, *(extra_roots or []), draft_root()]
    for root in roots:
        cand = Path(root) / arg
        if cand.exists():
            return cand
    raise FileNotFoundError(f"找不到草稿「{arg}」，找过：" + ", ".join(str(r) for r in roots))


def list_drafts(root: Path | None = None) -> list[Path]:
    base = Path(root) if root else PROJECT_DRAFTS
    if not base.exists():
        return []
    return sorted([d for d in base.iterdir() if d.is_dir() and (d / "draft_content.json").exists()])


def print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def print_lines(lines) -> None:
    for line in lines:
        print(line)
