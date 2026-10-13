#!/usr/bin/env python3
r"""联网学习剪辑软件的界面布局与功能，建成可复用的"界面记忆"。

做法：用网关原生联网检索（Responses API + web_search）查公开资料，
把结果整理成结构化记忆 config/<软件>.ui.json 与可读手册 content-library/<软件>-界面与功能记忆.md。

用法: python ui_learn.py --app 剪映专业版 [--out-suffix ui]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import research  # noqa: E402  （复用联网检索封装）

QUESTIONS: list[tuple[str, str]] = [
    ("主界面布局",
     "搜索并总结：{app}（Windows 专业版）主界面的整体布局，从左到右、从上到下有哪些区域和面板，"
     "各区域的常见名称是什么？给出 5-8 条要点和来源。"),
    ("顶部菜单与下拉项",
     "搜索并总结：{app} 左上角「菜单」下拉里有哪些项目（例如 文件、编辑、布局模式、更多操作、帮助、账号、"
     "全局设置、返回首页、退出等），每一项各自做什么？给出清单和来源。"),
    ("草稿列表与首页",
     "搜索并总结：在 {app} 里怎么回到草稿列表（首页）？草稿保存在哪个目录，草稿列表是启动时扫描还是实时刷新？"
     "第三方工具生成的草稿需要重启软件才会出现吗？给出要点和来源。"),
    ("常用快捷键",
     "搜索并总结：{app}（专业版）最常用的剪辑快捷键：分割、删除、撤销重做、导出、播放暂停、时间线缩放、"
     "添加字幕等。列出「功能 -> 按键」对照表和来源。"),
    ("草稿文件结构",
     "搜索并总结：{app} 草稿文件夹里有哪些文件（如 draft_content.json、draft_meta_info.json、"
     "root_meta_info.json 等），各自作用是什么？用脚本生成草稿时要注意什么？给出要点和来源。"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="联网学习界面布局")
    ap.add_argument("--app", default="剪映专业版")
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--out-suffix", default="ui")
    args = ap.parse_args()

    key = research.load_key()
    if not key:
        print("缺少 DEEPSEEK_API_KEY", file=sys.stderr)
        return 1

    report = {"软件": args.app, "维度": []}
    for name, tpl in QUESTIONS:
        prompt = tpl.format(app=args.app)
        print(f"[联网学习] {name} …")
        try:
            data = research.parse(research.ask(prompt, key, args.max_tokens))
        except Exception as exc:
            print(f"   失败: {type(exc).__name__} {str(exc)[:80]}")
            continue
        print(f"   检索 {len(data['查询'])} 次 / 打开 {len(data['来源'])} 页 / 回答 {len(data['回答'])} 字")
        report["维度"].append(data | {"名称": name})

    if not report["维度"]:
        print("[失败] 没有任何维度拿到结果")
        return 1

    slug = args.out_suffix
    raw = ROOT / "work" / f"{slug}-learn-raw.json"
    raw.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    mem = {
        "软件": args.app,
        "来源": "联网检索（Responses API + web_search），仅作参考，最终以本机实测为准",
        "更新时间": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "维度": {d["名称"]: {"要点": d["回答"], "来源": d["来源"]} for d in report["维度"]},
    }
    mem_path = ROOT / "config" / f"{slug}.ui.json"
    mem_path.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# {args.app} 界面与功能记忆（联网学习）", "",
             f"> 来源：网关联网检索，更新于 {mem['更新时间']}。网上资料仅作参考，坐标类信息以本机实测（config/ui_elements.json）为准。", ""]
    for d in report["维度"]:
        lines += [f"## {d['名称']}", "", d["回答"], ""]
        if d["来源"]:
            lines += ["**来源**", ""] + [f"- {s}" for s in d["来源"][:8]] + [""]
    md_path = ROOT / "content-library" / f"{args.app}-界面与功能记忆.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n[结构化记忆] {mem_path}")
    print(f"[可读手册]   {md_path}")
    print(f"[原始结果]   {raw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
