#!/usr/bin/env python3
r"""阶段2.5 校对：把人工/复核确认的修正写回逐字稿与 AI 识别结果。

为什么需要它：转写是 ASR 初稿，同音字、行业术语经常错；而识别结果（角色/高亮/钩子）
是按"原句文本"做键匹配的，只改逐字稿会造成两处不一致，剪辑时钩子句失效。

用法:
    python proofread.py                 # 列出待校对项
    python proofread.py 月子知识-0299    # 应用该素材的修正
    python proofread.py --all           # 应用全部修正
输出:
    02-transcript/<名称>.json/.txt/.srt  就地修正
    work/understand-<名称>.json          逐句/钩子句/高亮/删除建议/关键词 同步修正
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "corrections.json"


def hit(text: str, old: str) -> bool:
    """整句命中或包含命中都算；ASR 常把标点/空格去掉，这里做宽松比较。"""
    norm = lambda s: "".join(s.split()).strip("，。！？；、,.!?;：:")
    return norm(old) in norm(text)


def replace_all(value, old: str, new: str):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [replace_all(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: replace_all(v, old, new) for k, v in value.items()}
    return value


def apply_one(name: str, fixes: list[dict]) -> int:
    changed = 0
    tpath = ROOT / "02-transcript" / f"{name}.json"
    if not tpath.exists():
        print(f"[跳过] 没有逐字稿: {tpath.name}")
        return 0
    data = json.loads(tpath.read_text(encoding="utf-8"))
    for fix in fixes:
        old, new = fix["原"], fix["改"]
        if not any(hit(s.get("text", ""), old) for s in data.get("segments", [])):
            print(f"[无变化] 逐字稿里找不到：{old}")
            continue
        data = replace_all(data, old, new)
        changed += 1
        print(f"[校正] {old}  ->  {new}   （{fix.get('状态','')}）")
        tpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "02-transcript" / f"{name}.txt").write_text(
            "\n".join(s["text"] for s in data["segments"]), encoding="utf-8")

    if changed:
        # 字幕 SRT 重新生成，行号与时间码沿用逐字稿
        def fmt(sec: float) -> str:
            ms = int(round(sec * 1000))
            h, ms = divmod(ms, 3600000)
            m, ms = divmod(ms, 60000)
            s, ms = divmod(ms, 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        srt = [f"{i}\n{fmt(float(s['start']))} --> {fmt(float(s['end']))}\n{s['text']}\n"
               for i, s in enumerate(data["segments"], 1)]
        (ROOT / "02-transcript" / f"{name}.srt").write_text("\n".join(srt), encoding="utf-8")

    upath = ROOT / "work" / f"understand-{name}.json"
    if upath.exists():
        u = json.loads(upath.read_text(encoding="utf-8"))
        for fix in fixes:
            u = replace_all(u, fix["原"], fix["改"])
        upath.write_text(json.dumps(u, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[同步] 识别结果已更新: {upath.name}")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="AI cut 转写校对")
    ap.add_argument("name", nargs="?", help="素材名（对应 02-transcript/<名称>.json）")
    ap.add_argument("--all", action="store_true", help="应用全部素材的修正")
    args = ap.parse_args()

    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    table = cfg.get("修正", {})
    if not args.name and not args.all:
        total = 0
        for name, fixes in table.items():
            print(f"\n## {name}")
            for f in fixes:
                print(f"   [{f.get('状态','')}] {f['原']} -> {f['改']}")
                total += 1
        print(f"\n共 {total} 条。改完请把状态置为“已确认”并执行: python scripts/proofread.py <名称>")
        return 0

    names = list(table) if args.all else [args.name]
    done = 0
    for name in names:
        if name not in table:
            print(f"[跳过] 校对表里没有: {name}", file=sys.stderr)
            continue
        done += apply_one(name, table[name])
    print(f"\n完成，应用 {done} 条修正。")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
