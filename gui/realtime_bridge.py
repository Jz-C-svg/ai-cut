"""GUI 侧：从剪映草稿导入素材（v3.8.0 模板化剪辑 · P1 简化版）。

注意：本文件是 **GUI 辅助层**，与 `lib/aicut/realtime_bridge.py`（ai-auto-editor
实时打点桥接）同名但职责不同，二者互不影响。

P1 只做两件事：
  1. `list_jianying_drafts()`：扫描剪映草稿目录，列出最近草稿（按修改时间倒序）；
  2. `extract_materials_from_draft()`：读取草稿里的本地素材路径，加入素材库。

完整的实时画面流解析（边剪映播放边识别素材）放 P2，本版本不做。

剪映新版会把 draft_content.json 加密存储（首字节不是 '{'），此时无法直接读
materials 数组。本模块做了健壮性处理：
  · 优先按明文 JSON 解析 materials.videos / materials.images 的本地 path；
  · 解析失败（加密 / 损坏）时，回退到草稿目录递归扫描常见视频文件作为素材候选；
  · 只做"路径提取"，不搬运、不上传，素材本体仍在你本机。
"""
from __future__ import annotations
from aicut.portability import portable_path

import json
from datetime import datetime
from pathlib import Path

# 剪映默认草稿根目录（与 appconfig.draft_root 默认值一致）
DEFAULT_DRAFT_ROOT = (
    portable_path('user:AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft')
)

# 识别为"素材视频"的扩展名
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v"}


def list_jianying_drafts(draft_root: str | Path = DEFAULT_DRAFT_ROOT) -> list[dict]:
    """列出剪映草稿目录下的最近草稿。

    返回：[{name, path, mtime, mtime_str}, ...]，按修改时间倒序（最近在前）。
    目录不存在时返回空列表，不抛异常。
    """
    root = Path(draft_root)
    if not root.is_dir():
        return []
    out: list[dict] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        try:
            st = d.stat()
        except OSError:
            continue
        out.append({
            "name": d.name,
            "path": str(d),
            "mtime": st.st_mtime,
            "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def _parse_plain_draft(draft_path: Path) -> list[str] | None:
    """尝试明文解析 draft_content.json，返回视频/图片本地路径列表。

    返回 None 表示草稿主文件不是明文 JSON（多半被剪映加密），需要回退。
    """
    content_file = draft_path / "draft_content.json" if draft_path.is_dir() else draft_path
    if not content_file.exists():
        return None
    try:
        data = json.loads(content_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    paths: list[str] = []
    materials = data.get("materials") or {}
    for key in ("videos", "images", "audio"):
        for m in (materials.get(key) or []):
            if not isinstance(m, dict):
                continue
            fp = m.get("path") or m.get("file_name") or ""
            fp = str(fp)
            if fp and Path(fp).exists():
                paths.append(fp)
    return paths


def _scan_folder_videos(draft_path: Path) -> list[str]:
    """回退方案：在草稿目录及其子目录里找常见视频文件（素材本体）。"""
    out: list[str] = []
    for f in draft_path.rglob("*"):
        try:
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                out.append(str(f))
        except OSError:
            continue
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


def extract_materials_from_draft(draft_path: str | Path) -> list[str]:
    """从草稿目录提取本地素材路径。

    策略：先尝试明文解析 materials 数组；加密/失败则回退目录扫描。
    只返回真实存在于磁盘上的文件路径，去重，保持发现顺序。
    """
    p = Path(draft_path)
    if not p.exists():
        return []
    plain = _parse_plain_draft(p)
    if plain:
        return plain
    return _scan_folder_videos(p)


if __name__ == "__main__":  # 简单自测
    drafts = list_jianying_drafts()
    print(f"找到 {len(drafts)} 个草稿")
    for d in drafts[:5]:
        print(f"  {d['mtime_str']}  {d['name']}")
    if drafts:
        mats = extract_materials_from_draft(drafts[0]["path"])
        print(f"最新草稿素材 {len(mats)} 个：")
        for m in mats[:5]:
            print("   ", m)
