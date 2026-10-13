#!/usr/bin/env python3
r"""全网学习（中文源 + 外文源一起检索）→ 提炼要点 → 语义记忆。

检索通道：A=research.py 联网模型（需 key）；B=DuckDuckGo 直连（免 key，中英双查询）。
产出写入 memory.sqlite：facts 键 `web:<主题>`（兼容 learn_batch 去重）+ vec_items 语义索引。

用法:
  python learn_web_multi.py --topic "剪映 关键帧 变速"            # 中英一起查
  python learn_web_multi.py --topic "CapCut speed ramp" --lang en
  python learn_web_multi.py --topic "..." --limit-snippets 6 --no-llm   # 不调 LLM，直接存检索结果
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from aicut import memory_semantic as ms  # noqa: E402
from aicut import web_research as wr  # noqa: E402


def distill(topic: str, snippets: list[dict], provider: str = "auto") -> str:
    """用 LLM 把多来源检索结果提炼成要点文本；LLM 不可用则返回拼接摘要。"""
    material = "\n\n".join(
        f"[{s.get('title','')}]({s.get('url','')})\n{s.get('text','')}" for s in snippets[:8])
    try:
        import understand  # noqa: E402
        prompt = (
            f"下面是关于「{topic}」的多来源网页检索结果（含中文与英文来源）。"
            "请提炼 5-8 条可操作的做法要点，每条一句话并标注来源 URL。只输出要点列表。\n\n" + material)
        data, _prov = understand.llm_json(
            f"{prompt}\n\n以 JSON 输出：{{\"要点\": [\"...\"], \"来源\": [\"url\"]}}", provider)
        if data and data.get("要点"):
            points = [str(p) for p in data["要点"]][:8]
            for u in (data.get("来源") or [])[:10]:
                if u not in [s.get("url") for s in snippets]:
                    snippets.append({"title": "来源", "url": str(u), "text": ""})
            return "\n".join(f"- {p}" for p in points)
    except Exception as exc:  # noqa: BLE001
        print(f"[提炼] LLM 不可用（{type(exc).__name__}），降级为原文摘要", file=sys.stderr)
    return "\n".join(f"- {s.get('title','')}: {s.get('text','')[:160]} ({s.get('url','')})"
                     for s in snippets[:8])


def main() -> int:
    ap = argparse.ArgumentParser(description="全网学习（中文+外文）")
    ap.add_argument("--topic", required=True, help="学习主题")
    ap.add_argument("--lang", choices=["both", "zh", "en"], default="both")
    ap.add_argument("--limit-snippets", type=int, default=10)
    ap.add_argument("--provider", default="auto", choices=["auto", "deepseek", "ollama"])
    ap.add_argument("--no-llm", action="store_true", help="不调 LLM，直接存检索原文")
    args = ap.parse_args()

    result = wr.search_all(args.topic, lang=args.lang, max_results=args.limit_snippets)
    print(f"[检索] 通道: {', '.join(result['channels']) or '无可用通道'}；"
          f"结果 {result['count']} 条；来源 {len(result['sources'])} 个")
    for s in result["snippets"][:6]:
        print(f"  - {s.get('title','')[:60]}  {s.get('url','')[:80]}")
    if not result["snippets"]:
        print("[失败] 所有检索通道均无结果（检查网络/DEEPSEEK_API_KEY）")
        return 1

    summary = (result["snippets"][0].get("text", "") if args.no_llm
               else distill(args.topic, result["snippets"], args.provider))

    # 写 facts（键格式兼容 learn_batch 的 web: 去重）
    from aicut.memory import open_memory  # noqa: E402
    mem = open_memory()
    mem.set_fact(f"web:{args.topic}", {
        "查询": args.topic, "来源": result["sources"],
        "要点": [{"要点": ln.lstrip('- ').strip(), "来源": ""} for ln in summary.splitlines() if ln.startswith("-")],
        "原文": summary[:6000],
    }, source="web")

    # 写语义索引
    ms.upsert(f"web:{args.topic}", f"主题:{args.topic}\n{summary}", kind="web",
              meta={"sources": result["sources"][:10], "lang": args.lang})
    print(f"[入库] facts=web:{args.topic}；语义索引已更新（向量库共 {ms.count()} 条）")
    print("\n要点:\n" + summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
