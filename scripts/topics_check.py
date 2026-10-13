#!/usr/bin/env python3
r"""主题清单校验与修复。

JSON 里把直引号（"…"）当强调号是常见错误：文件会直接解析失败。这里做两件事：
  --check  校验能不能读、字段齐不齐（默认）
  --fix    把值内部的直引号成对换成「」，再校验（可重复跑）

算法：一行形如   "键": "值",   ，值的终点取该行**最后一个**引号（它是 JSON 的收尾引号），
中间出现的直引号才是误用的强调号，按出现顺序成对换成「」。

用法:
  python topics_check.py                                    # 校验两份清单
  python topics_check.py --fix
  python topics_check.py config/learn_topics_ai_editing.json --fix
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402
from aicut.paths import CONFIG_DIR  # noqa: E402

cli.setup()

REQUIRED = ("id", "主题", "检索词", "面向")
FACES = ("wiki", "github", "paper", "doc", "general", "en")
OPEN_Q, CLOSE_Q = "「", "」"


def fix_quotes(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").split("\n")
    out: list[str] = []
    fixed = 0
    for line in lines:
        if '": "' in line and line.count('"') > 4:
            start = line.index('": "') + len('": "')
            end = line.rfind('"')
            if end > start:
                body = line[start:end].strip().strip('"')
                chars: list[str] = []
                toggle = 0
                for ch in body:
                    if ch == '"':
                        chars.append(OPEN_Q if toggle == 0 else CLOSE_Q)
                        toggle ^= 1
                    else:
                        chars.append(ch)
                line = line[:start] + "".join(chars) + line[end:]
                fixed += 1
        out.append(line)
    path.write_text("\n".join(out), encoding="utf-8")
    return fixed


def check(path: Path) -> tuple[bool, list[str]]:
    if not path.exists():
        return False, [f"文件不存在：{path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return False, [f"JSON 解析失败：{exc}"]
    topics = data.get("主题") or []
    notes: list[str] = []
    if not topics:
        notes.append("清单是空的")
    for i, entry in enumerate(topics, 1):
        missing = [k for k in REQUIRED if not entry.get(k)]
        if missing:
            notes.append(f"第 {i} 条缺字段：{'、'.join(missing)}")
        face = entry.get("面向")
        if face and face not in FACES:
            notes.append(f"第 {i} 条面向不认识：{face}（可选 {', '.join(FACES)}）")
    ids = [e.get("id") for e in topics]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        notes.append("id 重复：" + "、".join(sorted(dup)))
    return (not notes), notes


def main() -> int:
    ap = argparse.ArgumentParser(description="主题清单校验/修复")
    ap.add_argument("files", nargs="*", help="清单文件（默认两份内置清单）")
    ap.add_argument("--fix", action="store_true", help="先修直引号再校验")
    args = ap.parse_args()

    files = [Path(f) for f in args.files] or [
        CONFIG_DIR / "learn_topics.json",
        CONFIG_DIR / "learn_topics_ai_editing.json",
    ]
    bad = 0
    for path in files:
        if args.fix:
            n = fix_quotes(path)
            if n:
                print(f"{path.name}：修了 {n} 行直引号")
        ok, notes = check(path)
        if ok:
            data = json.loads(path.read_text(encoding="utf-8"))
            faces: dict[str, int] = {}
            for entry in data.get("主题") or []:
                key = str(entry.get("面向"))
                faces[key] = faces.get(key, 0) + 1
            summary = "、".join(f"{k} {v}" for k, v in sorted(faces.items()))
            print(f"{path.name}：OK · {len(data.get('主题') or [])} 条 · {summary}")
        else:
            bad += 1
            print(f"{path.name}：有问题")
            for note in notes[:8]:
                print("  ! " + note)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
