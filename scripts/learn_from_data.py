#!/usr/bin/env python3
r"""跟数据学：把后台数据和你的基线比，得出"下一步只改哪一个变量"。

读抖音后台导出（csv/xlsx），算基线（最近 N 条中位数），再按风格分组比一比，
给出可执行、单变量的建议，写进 memory.sqlite 的 style_params（规划器可读）。

**不会动 content-library/styles.json**：数据只产出建议与记忆，风格卡的最终修改留给
你确认——这样任何一次回灌都能在记忆库里查、也能被下一条数据推翻。

用法:
  python learn_from_data.py --data "08-data/后台导出.csv"
  python learn_from_data.py --data x.xlsx --last 20 --style-column 风格 --apply
  python learn_from_data.py --data x.csv --json
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402
from aicut.memory import open_memory  # noqa: E402
from aicut.paths import DATA  # noqa: E402

cli.setup()

# 与 analyze_data.py 保持一致的表头线索
HINTS = {
    "views": ["播放", "曝光"],
    "finish": ["完播"],
    "avg_watch": ["平均播放", "平均观看"],
    "likes": ["点赞"],
    "comments": ["评论"],
    "shares": ["分享", "转发"],
    "fans": ["涨粉", "粉丝增量", "新增粉丝"],
    "title": ["标题", "作品", "视频", "名称"],
    "style": ["风格", "类型", "体裁"],
}


def load_rows(path: Path) -> list[dict]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        header = [str(c) if c is not None else "" for c in rows[0]]
        return [dict(zip(header, r)) for r in rows[1:] if any(r)]
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def map_columns(row: dict, style_column: str | None = None) -> dict:
    out: dict[str, str] = {}
    for field, hints in HINTS.items():
        if field == "style" and style_column:
            out[field] = style_column
            continue
        for key in row:
            if any(h in str(key) for h in hints):
                out[field] = key
                break
    return out


def to_num(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "").replace("+", "")
    if not text or text in ("-", "--"):
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    return num


def main() -> int:
    ap = argparse.ArgumentParser(description="从后台数据学参数")
    ap.add_argument("--data", required=True, help="后台导出 csv/xlsx")
    ap.add_argument("--last", type=int, default=20, help="用最近多少条算基线（默认 20）")
    ap.add_argument("--style-column", help="风格列名（不填则自动猜 风格/类型/体裁）")
    ap.add_argument("--apply", action="store_true", help="把建议写进记忆库（默认只报告）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = Path(args.data)
    if not path.exists():
        alt = DATA / args.data
        if alt.exists():
            path = alt
        else:
            raise SystemExit(f"找不到数据文件：{args.data}")
    rows = load_rows(path)
    rows = [r for r in rows if any(str(v or "").strip() for v in r.values())]
    if not rows:
        raise SystemExit("表里没有数据行")
    cols = map_columns(rows[0], args.style_column)
    window = rows[-args.last:] if args.last and len(rows) > args.last else rows

    def series(field: str, data=window) -> list[float]:
        key = cols.get(field)
        if not key:
            return []
        return [n for n in (to_num(r.get(key)) for r in data) if n is not None]

    baseline = {field: (statistics.median(vals) if vals else None)
                for field, vals in ((f, series(f)) for f in ("views", "finish", "likes", "comments", "shares", "fans"))}
    report: dict = {"file": str(path), "行数": len(rows), "基线样本": len(window),
                    "列映射": cols, "基线": baseline, "建议": [], "风格对比": {}}

    style_key = cols.get("style")
    if style_key:
        groups: dict[str, list[dict]] = {}
        for r in window:
            name = str(r.get(style_key) or "").strip()
            if name:
                groups.setdefault(name, []).append(r)
        for name, items in groups.items():
            if len(items) < 2:
                continue
            stat = {}
            for field in ("views", "finish", "likes", "fans"):
                key = cols.get(field)
                vals = [n for n in (to_num(i.get(key)) for i in items) if n is not None] if key else []
                stat[field] = statistics.median(vals) if vals else None
            report["风格对比"][name] = {"条数": len(items), **stat}
            for field, label, unit in (("finish", "完播率", "%"), ("views", "播放量", "")):
                if stat.get(field) is None or not baseline.get(field):
                    continue
                diff = (stat[field] - baseline[field]) / baseline[field]
                if abs(diff) >= 0.15:
                    direction = "高" if diff > 0 else "低"
                    report["建议"].append({
                        "变量": f"风格卡「{name}」的使用",
                        "依据": f"{label}比你的基线{direction} {abs(diff):.0%}（{stat[field]:.0f}{unit} vs {baseline[field]:.0f}{unit}，{len(items)} 条）",
                        "动作": ("下一条继续用这个风格，其他变量不动" if diff > 0 else "换一个风格卡再测一条，其他变量不动"),
                        "风格": name, "字段": field, "差异": round(diff, 3),
                    })
    else:
        report["建议"].append({"变量": "风格列", "依据": "表里没有风格/类型列，无法按风格对比",
                              "动作": "导出时带上风格列，或在决策单里记风格卡 id"})

    if not report["建议"]:
        report["建议"].append({"变量": "无显著差异", "依据": "各风格相对基线的差异都在 15% 以内",
                              "动作": "继续按现有风格跑，攒够 20 条再看"})

    # 最好/最差各一条，给人看
    views_key = cols.get("views")
    if views_key:
        ranked = sorted([r for r in window if to_num(r.get(views_key)) is not None],
                        key=lambda r: to_num(r.get(views_key)) or 0)
        if ranked:
            title_key = cols.get("title")
            pick = lambda r: {  # noqa: E731
                "标题": (str(r.get(title_key))[:40] if title_key else ""),
                "播放": to_num(r.get(views_key)),
            }
            report["最好的一条"] = pick(ranked[-1])
            report["最差的一条"] = pick(ranked[0])

    if args.apply:
        mem = open_memory()
        mem.set_style(f"数据回灌-{time.strftime('%Y%m%d')}",
                      {"基线": baseline, "风格对比": report["风格对比"]},
                      note="来自 learn_from_data.py；只作建议，未改动 content-library")
        for item in report["建议"]:
            if item.get("风格"):
                mem.add_preference(f"{item['变量']}：{item['动作']}（{item['依据']}）",
                                   evidence=str(path.name), confidence=0.6, source="data")
        report["已写库"] = True

    if args.json:
        cli.print_json(report)
        return 0

    print(f"数据：{path.name} · {len(rows)} 行（基线用最近 {len(window)} 条）")
    for field, label in (("views", "播放"), ("finish", "完播"), ("likes", "点赞"), ("comments", "评论"), ("fans", "涨粉")):
        if baseline.get(field) is not None:
            print(f"  基线{label}中位数：{baseline[field]:.0f}")
    if report["风格对比"]:
        print("按风格看：")
        for name, stat in report["风格对比"].items():
            print(f"  {name}（{stat['条数']} 条）播放 {stat['views'] or 0:.0f} · 完播 {stat['finish'] or 0:.0f}")
    print("建议（一次只改一个变量）：")
    for item in report["建议"]:
        print(f"  · {item['变量']}　{item['依据']}　→ {item['动作']}")
    if args.apply:
        print("\n已写进记忆库（style_params + preferences）；content-library/styles.json 未改动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
