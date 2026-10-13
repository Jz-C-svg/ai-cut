#!/usr/bin/env python3
r"""联网检索比对：把本片内容与网上同类做法做对照，输出可迁移的参考。

用网关原生的联网搜索工具（Responses API + web_search），实时检索并打开公开网页，
产出参考要点与来源链接。只迁移结构与做法，不搬运他人表达。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://api.deepseek.com/v1/responses"
MODEL = "deepseek-v4-pro"

QUESTIONS = [
    ("同类内容结构", "搜索并总结：{topic} 这个主题在抖音短视频里，播放量高的内容通常怎么开头、怎么组织结构？给出 3-5 条要点和来源。"),
    ("用户关心点", "搜索并总结：观众在看 {topic} 相关内容时，最常问的问题、最常见的误解是什么？给出 3-5 条要点和来源。"),
    ("合规与限流", "搜索并总结：发布 {topic} 这类内容，平台上最容易踩的违规点与限流原因是什么？给出 3-5 条要点和来源。"),
]


def load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                          "[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')"],
                         capture_output=True, text=True, timeout=30)
    return (out.stdout or "").strip()


def ask(prompt: str, key: str, tokens: int = 1800) -> dict:
    payload = {"model": MODEL, "input": prompt, "tools": [{"type": "web_search"}],
               "max_output_tokens": tokens}
    req = urllib.request.Request(URL, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    return json.loads(urllib.request.urlopen(req, timeout=600).read().decode("utf-8"))


def parse(resp: dict) -> dict:
    text, sources, queries = "", [], []
    for item in resp.get("output") or []:
        t = item.get("type")
        if t == "message":
            text += "".join(c.get("text", "") for c in item.get("content", []))
        elif t == "web_search_call":
            action = item.get("action") or {}
            if action.get("type") == "search":
                queries += action.get("queries") or []
            if action.get("type") == "open_page" and item.get("status") == "completed":
                url = str(action.get("url", "")).split("#ws_call_id")[0]
                if url:
                    sources.append(url)
    return {"回答": text.strip(), "查询": queries, "来源": sorted(set(sources))}

def main() -> int:
    ap = argparse.ArgumentParser(description="联网检索比对")
    ap.add_argument("--topic", required=True, help="本片主题，例如 月子中心 腹围增长")
    ap.add_argument("--transcript", help="逐字稿 JSON（可选，取前几句补充上下文）")
    ap.add_argument("--out", default="work/research.json")
    ap.add_argument("--max-tokens", type=int, default=1800)
    args = ap.parse_args()

    key = load_key()
    if not key:
        print("缺少 DEEPSEEK_API_KEY", file=sys.stderr)
        return 1

    context = ""
    if args.transcript:
        p = Path(args.transcript)
        if not p.is_absolute():
            p = ROOT / p
        if p.exists():
            segs = json.loads(p.read_text(encoding="utf-8")).get("segments", [])
            context = "本片讲的是：" + "".join(s.get("text", "") for s in segs[:4])
            print(f"[上下文] {context[:60]}…")

    report = {"主题": args.topic, "上下文": context, "维度": []}
    for name, tpl in QUESTIONS:
        prompt = tpl.format(topic=args.topic) + ("\n" + context if context else "")
        print(f"[联网检索] {name} …")
        try:
            data = parse(ask(prompt, key, args.max_tokens))
        except Exception as exc:
            print(f"   失败: {type(exc).__name__} {str(exc)[:70]}")
            continue
        print(f"   检索 {len(data['查询'])} 次，打开 {len(data['来源'])} 个页面，回答 {len(data['回答'])} 字")
        for s in data["来源"][:4]:
            print(f"     来源: {s[:88]}")
        report["维度"].append(data | {"名称": name})

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {out}")
    print("提醒：只迁移结构与做法，不搬运他人表达；来源链接仅作参考，发布前按平台规则复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
