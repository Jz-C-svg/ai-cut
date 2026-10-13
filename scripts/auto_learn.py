# -*- coding: utf-8 -*-
"""
auto_learn.py — 自动检索学习器
走外网代理检索视频剪辑教程/爆款案例元数据，用本地 Ollama 模型蒸馏剪辑手法，
追加为结构化学习记录到 content-library/learnlog/，供 styles 卡持续进化。

用法：
  python auto_learn.py --query "AI video editing tutorial" --platform youtube --limit 10
  python auto_learn.py --daily   # 每日自动跑（定时任务用）
"""
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path
import argparse, json, os, re, time, datetime, sys

ROOT = portable_path('project:')
LEARN_DIR = os.path.join(ROOT, "content-library", "learnlog")
PROXY = "http://127.0.0.1:10808"
OLLAMA = "http://127.0.0.1:11434/api/generate"
MODEL = "gemma4:e4b-opt"

os.makedirs(LEARN_DIR, exist_ok=True)

PROXIES = {"http": PROXY, "https": PROXY}


def http_get_json(url, params=None, timeout=20):
    import requests
    r = requests.get(url, params=params, proxies=PROXIES, timeout=timeout)
    r.raise_for_status()
    return r.json()


def search_youtube(query, limit=8):
    """走代理用 YouTube Data v3 风格搜索（无需 key：用 Invidious/公开搜索）"""
    hits = []
    try:
        # invidious 公开实例搜索
        data = http_get_json(
            "https://inv.nadeko.net/api/v1/search",
            params={"q": query, "type": "video", "sort_by": "relevance"},
            timeout=15,
        )
        for v in data[:limit]:
            hits.append({
                "platform": "youtube",
                "title": v.get("title", ""),
                "url": "https://www.youtube.com/watch?v=" + v.get("videoId", ""),
                "desc": (v.get("description") or "")[:300],
                "channel": v.get("author", ""),
                "views": v.get("viewCount", 0),
            })
    except Exception as e:
        print(f"[warn] youtube 搜索失败: {e}")
    return hits


def distill(llm, item):
    """用本地模型从教程标题/描述蒸馏剪辑手法"""
    prompt = f"""你是短视频剪辑专家。从以下教程信息中，提取可参数化的剪辑手法（节奏、转场、字幕、BGM、镜头、调色、结构）。
只输出 JSON：{{"手法要点":[..], "适用风格":"", "参数建议":{{}}, "新颖度":"高/中/低"}}

教程标题：{item['title']}
描述：{item['desc']}
"""
    try:
        r = llm.post(OLLAMA.replace("/generate", "/chat"),
                     json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                           "stream": False, "options": {"temperature": 0.2, "num_ctx": 8192}},
                     timeout=120, proxies=PROXIES if False else None)
        txt = r.json()["message"]["content"]
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else {"raw": txt[:400]}
    except Exception as e:
        return {"error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default=None)
    ap.add_argument("--platform", default="youtube")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--daily", action="store_true")
    args = ap.parse_args()

    queries = [args.query] if args.query else [
        "short video editing tutorial 2026",
        "vlog pacing transition technique",
        "talking head captions style",
    ]
    if args.daily:
        queries = queries[:2]

    import requests as llm
    all_records = []
    for q in queries:
        print(f"[learn] 检索: {q}")
        hits = search_youtube(q, args.limit)
        for h in hits:
            h["distill"] = distill(llm, h)
            all_records.append(h)
            time.sleep(1)

    today = datetime.date.today().isoformat()
    out = os.path.join(LEARN_DIR, f"{today}.json")
    payload = {"date": today, "query": queries, "count": len(all_records), "records": all_records}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[done] 学习记录已写入: {out} ({len(all_records)} 条)")
    for r in all_records:
        nov = r.get("distill", {}).get("新颖度", "?")
        print(f"  - [{nov}] {r['title'][:50]}")


if __name__ == "__main__":
    main()
