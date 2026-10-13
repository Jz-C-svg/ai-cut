# -*- coding: utf-8 -*-
"""项目自检工具（project_audit）：多余内容 / 孤儿模块 / 缺口审计

用法：
  python scripts/project_audit.py           # 完整审计（引用/垃圾/冗余/大目录/配置）
  python scripts/project_audit.py --quick   # 只输出垃圾文件 + 孤儿 + 大目录

输出：控制台结构化报告，供人工决策清理与补缺。
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
SCRIPTS = ROOT / "scripts"
AICUT = LIB / "aicut"


def _py_files(d: Path):
    return [p for p in d.rglob("*.py") if "__pycache__" not in str(p)]


def _dir_mb(d: Path) -> float:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e6


def find_junk() -> list[tuple[str, int]]:
    """垃圾/临时/缓存文件：__pycache__、pyc、patch_*、tmp/bak/orig"""
    junk = []
    for d in [LIB, SCRIPTS, ROOT / "work", ROOT / "app"]:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                sp = str(p)
                if "__pycache__" in sp or p.suffix in (".pyc", ".pyo", ".tmp", ".bak", ".orig"):
                    junk.append((sp, p.stat().st_size))
                    continue
                # 只把 work/ 下的 patch_* 当临时补丁；lib/ 下的 patch_apply.py 是正式模块
                if p.name.startswith("patch_") and "lib" not in sp:
                    junk.append((sp, p.stat().st_size))
                    continue
                if p.name.startswith("tmp"):
                    junk.append((sp, p.stat().st_size))
    junk.sort(key=lambda x: -x[1])
    return junk


def find_orphans(blob: str, names: list[str]) -> list[str]:
    """全文引用 0 次的模块（真孤儿；插件式 skills_*.py 经 __init__ 注册 1 次属正常）"""
    return [n for n in names
            if len(re.findall(r"\b%s\b" % re.escape(n), blob)) == 0]


def big_dirs(top_n: int = 6, max_children: int = 6) -> list[str]:
    out = []
    for d in ["models", "work", "external", "venv", "07-output", "08-data"]:
        dd = ROOT / d
        if not dd.exists():
            continue
        lines = [f"[{d}] 合计 {_dir_mb(dd):.1f} MB"]
        children = sorted((c for c in dd.iterdir() if c.is_dir()),
                          key=lambda c: -_dir_mb(c))[:max_children]
        for c in children:
            lines.append(f"    {c.name:<40} {_dir_mb(c):>9.1f} MB")
        out.append("\n".join(lines))
    return out[:top_n]


def main() -> None:
    quick = "--quick" in sys.argv
    print("=" * 64)
    print("AI cut 项目自检")
    print("=" * 64)

    # 1) 垃圾文件
    print("\n[1] 垃圾/临时/缓存文件（可清理，自动重建/无影响）")
    junk = find_junk()
    for sp, sz in junk:
        print(f"  {sp}  ({sz // 1024}KB)")
    print(f"  小计 {len(junk)} 个，共 {sum(sz for _, sz in junk) // 1024} KB")

    if quick:
        print("\n[大目录]")
        for b in big_dirs():
            print("  " + b.replace("\n", "\n  "))
        return

    # 2) 孤儿模块
    print("\n[2] lib\\aicut 孤儿模块（全文引用 ≤1 次，需人工确认）")
    srcs = [(p, io.open(p, encoding="utf-8", errors="ignore").read())
            for base in (LIB, SCRIPTS) for p in _py_files(base)]
    blob = "\n".join(s for _, s in srcs)
    names = [p.stem for p in _py_files(AICUT) if p.stem != "__init__"]
    for o in find_orphans(blob, names):
        print(f"  ⚠ {o}.py")

    # 3) 可疑重复模块
    print("\n[3] 可疑重复/分工模块")
    for a, b in [("edit.py", "edit2.py"), ("frame_extractor.py", "snapshot.py"),
                 ("vision.py", "perception.py"), ("diff.py", "compare.py")]:
        fa, fb = AICUT / a, AICUT / b
        if fa.exists() and fb.exists():
            cnt_a = len(re.findall(r"\b%s\b" % re.escape(fa.stem), blob))
            cnt_b = len(re.findall(r"\b%s\b" % re.escape(fb.stem), blob))
            print(f"  {a} ({fa.stat().st_size // 1024}KB, 引用{cnt_a}) vs "
                  f"{b} ({fb.stat().st_size // 1024}KB, 引用{cnt_b})")

    # 4) 大目录
    print("\n[4] 大目录构成（可瘦身项）")
    for b in big_dirs():
        print("  " + b.replace("\n", "\n  "))

    # 5) 废弃目录
    print("\n[5] 废弃/历史目录")
    for d in sorted(ROOT.iterdir()):
        if d.is_dir() and (d.name.startswith("_trash") or d.name.startswith("_old")
                           or d.name.startswith("tmp")):
            print(f"  {d.name}  ({_dir_mb(d):.1f}MB)")


if __name__ == "__main__":
    main()
