#!/usr/bin/env python3
r"""能力矩阵：主体现在到底能干什么、还缺什么、覆盖率多少。

数据来源两部分：
  已有能力   从技能注册表实时读（技能 id / 通道 / 参数 / 是否有验收规格 / 历史成功率）
  目标能力   TARGETS 里列的"全流程剪辑任务"清单，逐项标注现状（已实现/部分/缺口）

用法:
  python capability_matrix.py                 # 打印矩阵与覆盖率
  python capability_matrix.py --write         # 同时写到 docs/能力矩阵.md
  python capability_matrix.py --json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, skills  # noqa: E402
from aicut.memory import open_memory  # noqa: E402

cli.setup()

# 目标能力清单：能力 → (通道, 覆盖它的技能, 现状)
TARGETS: list[tuple[str, str, str, str]] = [
    ("删除区间（画面/字幕/特效联动）", "draft", "delete_range,delete_head,delete_tail", "已实现"),
    ("剪掉停顿/静音（含安全阀）", "draft", "delete_silences", "已实现"),
    ("剪掉口误与废话", "draft", "delete_ai_cuts", "已实现"),
    ("压到目标时长", "draft", "trim_to", "已实现"),
    ("切分片段", "draft", "split_at", "已实现"),
    ("加/改字幕", "draft", "add_subtitle", "已实现"),
    ("字幕样式（字体/描边/行宽）", "draft", "generate_draft(整片),add_subtitle(沿用模板)", "部分"),
    ("字幕关键词高亮", "draft", "generate_draft", "部分"),
    ("加转场（433 种）", "draft", "add_transition", "已实现"),
    ("加滤镜（972 种）", "draft", "add_filter", "已实现"),
    ("加特效", "draft", "-", "缺口"),
    ("关键帧动画（缩放/位移）", "draft", "-", "缺口"),
    ("多轨叠加 / B-roll", "draft", "generate_draft(broll)", "部分"),
    ("音量（分贝/包络/闪避）", "draft", "set_gain_db", "部分"),
    ("素材准备（标准化+转写）", "shell", "prepare_material", "已实现"),
    ("按风格整片出片", "shell", "generate_draft", "已实现"),
    ("打开草稿/导出/重启", "ui", "ui_open_draft,ui_export,ui_refresh", "已实现"),
    ("界面按键（白名单）", "ui", "ui_press", "已实现"),
    ("剪映专有配方（花字/云字体等）", "ui", "ui_recipe", "部分（待排练）"),
    ("遮罩 / 抠像", "ui", "-", "缺口"),
    ("重构比例（横转竖跟主体）", "draft/ui", "-", "缺口"),
    ("稳定 / 调色 / LUT", "render/ui", "-", "缺口"),
    ("封面（选帧+花字+文案）", "draft/ui", "generate_draft", "部分"),
    ("导出成片（含批量）", "render", "generate_draft,export_video.py", "部分"),
    ("成片评审（七维）", "-", "-", "缺口"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="能力矩阵与覆盖率")
    ap.add_argument("--write", action="store_true", help="写到 docs/能力矩阵.md")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mem = open_memory()
    runs = {s["id"]: s for s in mem.skills()}
    catalog = skills.catalog()
    live = []
    for skill in catalog:
        stat = runs.get(skill.id, {})
        live.append({
            "id": skill.id, "通道": skill.channel, "目标": skill.goal,
            "参数": list((skill.args or {}).keys()),
            "有验收": bool(skill.verify) or skill.id in ("add_subtitle", "add_transition",
                                                          "add_filter", "set_gain_db", "split_at"),
            "成功率": stat.get("success"), "跑过": stat.get("runs") or 0,
        })

    done = sum(1 for _, _, _, s in TARGETS if s == "已实现")
    part = sum(1 for _, _, _, s in TARGETS if s.startswith("部分"))
    gap = sum(1 for _, _, _, s in TARGETS if s == "缺口")
    total = len(TARGETS)
    verified = sum(1 for item in live if item["有验收"])

    payload = {
        "技能总数": len(catalog),
        "有验收规格的技能": verified,
        "目标能力": total, "已实现": done, "部分": part, "缺口": gap,
        "覆盖率": round((done + part * 0.5) / total, 3),
        "硬覆盖率(已实现/目标)": round(done / total, 3),
        "技能": live, "矩阵": [{"能力": a, "通道": b, "技能": c, "现状": d} for a, b, c, d in TARGETS],
    }

    if args.json:
        cli.print_json(payload)
    else:
        print(f"技能 {len(catalog)} 个（{verified} 个带验收规格，占 {verified / max(len(catalog), 1):.0%}）")
        print(f"目标能力 {total} 项：已实现 {done} · 部分 {part} · 缺口 {gap}"
              f" → 覆盖率 {(done + part * 0.5) / total:.0%}（硬覆盖 {done / total:.0%}）")
        print()
        print(f"{'能力':<28}{'通道':<10}{'现状':<8}技能")
        for name, chan, who, status in TARGETS:
            print(f"{name:<28}{chan:<10}{status:<8}{who}")
        print()
        print("已有技能：")
        for item in live:
            rate = f"{item['成功率']:.0%}" if item["成功率"] else "-"
            print(f"  {item['id']:<18}{item['通道']:<8}验收{'有' if item['有验收'] else '无'}"
                  f"  成功率 {rate}  跑过 {item['跑过']} 次  {item['目标'][:26]}")

    if args.write:
        out = ROOT / "docs" / "能力矩阵.md"
        lines = ["# 能力矩阵（自动生成，勿手改）", "",
                 f"- 技能总数：**{len(catalog)}**，其中带验收规格 **{verified}** 个",
                 f"- 目标能力 {total} 项：已实现 **{done}** · 部分 **{part}** · 缺口 **{gap}**",
                 f"- 覆盖率 **{(done + part * 0.5) / total:.0%}**（硬覆盖 {done / total:.0%}）", "",
                 "| 能力 | 通道 | 现状 | 覆盖它的技能 |", "| --- | --- | --- | --- |"]
        for name, chan, who, status in TARGETS:
            lines.append(f"| {name} | {chan} | {status} | {who} |")
        lines += ["", "## 已有技能", "", "| 技能 | 通道 | 验收 | 成功率 | 跑过 | 目标 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for item in live:
            rate = f"{item['成功率']:.0%}" if item["成功率"] else "-"
            lines.append(f"| {item['id']} | {item['通道']} | {'有' if item['有验收'] else '无'} |"
                         f" {rate} | {item['跑过']} | {item['目标']} |")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n已写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
