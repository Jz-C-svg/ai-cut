#!/usr/bin/env python3
r"""按清单批量学习：多种检索词、多来源（Wiki / GitHub / 论文 / 官方文档）。

清单在 config/learn_topics.json，每条主题带"面向"，决定检索策略与提问写法。
学过的按 facts 键去重——重复运行只会补没学过的，所以适合反复/定时跑。

用法:
  python learn_batch.py --list                       # 看清单与学习状态
  python learn_batch.py --run --limit 3              # 这次学 3 条
  python learn_batch.py --run --category github      # 只学 GitHub 面向的
  python learn_batch.py --run --only gh-scenedetect,wiki-editing-basics
  python learn_batch.py --add "剪映 音频降噪怎么做" --face github
  python learn_batch.py --run --minutes 20 --force    # 给个时间预算，--force 重学已学过的
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, learn  # noqa: E402
from aicut.memory import open_memory  # noqa: E402
from aicut.paths import DATA, LOGS, CONFIG_DIR  # noqa: E402

cli.setup()

TOPICS = CONFIG_DIR / "learn_topics.json"
LOG = LOGS / "learn.log"


def load_topics() -> dict:
    if not TOPICS.exists():
        return {"主题": []}
    return json.loads(TOPICS.read_text(encoding="utf-8"))


def save_topics(data: dict) -> None:
    TOPICS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def learned_keys() -> set[str]:
    mem = open_memory()
    return {k[4:] for k in (mem.facts() or {}) if k.startswith("web:")}


def log(line: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    text = f"{stamp} | {line}"
    print(text, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def pick(entries: list[dict], args, done: set[str]) -> list[dict]:
    out = []
    for e in entries:
        if args.category and str(e.get("面向")) != args.category:
            continue
        if args.only and e.get("id") not in args.only:
            continue
        if not args.force and e.get("主题") in done:
            continue
        out.append(e)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="按清单批量学习")
    ap.add_argument("--list", action="store_true", help="看清单与学习状态")
    ap.add_argument("--run", action="store_true", help="开始学")
    ap.add_argument("--limit", type=int, default=5, help="这次最多学几条")
    ap.add_argument("--minutes", type=float, default=0, help="时间预算（分钟），0=不限")
    ap.add_argument("--category", help="只看某个面向：wiki/github/paper/doc/general")
    ap.add_argument("--only", help="只学这些 id（逗号分隔）")
    ap.add_argument("--force", action="store_true", help="已学过的也重学")
    ap.add_argument("--add", help="往清单里加一条主题")
    ap.add_argument("--face", default="general", help="新主题的面向")
    ap.add_argument("--terms", help="新主题的检索词（不填就用主题名）")
    ap.add_argument("--topics", help="主题清单文件（默认 config/learn_topics.json）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.topics:
        global TOPICS
        TOPICS = Path(args.topics)
    if args.only:
        args.only = {x.strip() for x in args.only.split(",") if x.strip()}

    if args.add:
        data = load_topics()
        entry = {
            "id": "custom-" + str(abs(hash(args.add)) % 10 ** 6),
            "主题": args.add,
            "检索词": args.terms or args.add,
            "面向": args.face,
            "拿来干什么": "（自加）",
        }
        data.setdefault("主题", []).append(entry)
        save_topics(data)
        print(f"已加入清单：{args.add}（id {entry['id']}，面向 {args.face}）")
        return 0

    data = load_topics()
    entries = data.get("主题") or []
    done = learned_keys()

    if args.list or not args.run:
        print(f"清单共 {len(entries)} 条，已学 {len(done & {e.get('主题') for e in entries})} 条")
        for e in entries:
            mark = "已学" if e.get("主题") in done else "    "
            print(f"  [{mark}] {e.get('面向'):<8} {e.get('id'):<26} {e.get('主题')}")
        return 0

    todo = pick(entries, args, done)
    if not todo:
        print("没有要学的了（清单里的都学过了；要重学加 --force，要加新主题用 --add）")
        return 0
    batch = todo[: args.limit]
    deadline = time.time() + args.minutes * 60 if args.minutes else None
    log(f"开始学习：{len(batch)}/{len(todo)} 条待学（清单 {len(entries)} 条）")

    results = []
    for entry in batch:
        if deadline and time.time() > deadline:
            log("到时间预算，收工（下次接着学）")
            break
        topic = str(entry.get("主题"))
        question = learn.build_question(entry)
        started = time.time()
        try:
            face = str(entry.get("面向") or "general")
            res = learn.web_learn_custom(topic, question,
                                         want_recipes=face in ("github", "general"))
        except Exception as exc:  # noqa: BLE001
            res = {"主题": topic, "要点数": 0, "配方数": 0, "来源数": 0, "来源": [],
                   "原文长度": 0, "error": f"{type(exc).__name__}: {exc}"[:160]}
        if not res.get("要点数"):
            # 没产出要点 = 这次没学成。删掉 facts 键，留待下一轮重试（否则会被永久跳过）
            _mem = open_memory()
            _mem.db.execute("DELETE FROM facts WHERE key=?", (f"web:{topic}",))
            _mem.db.commit()
            res["retry"] = True
        res["面向"] = entry.get("面向")
        res["耗时秒"] = round(time.time() - started, 1)
        results.append(res)
        log(f"  {topic}｜{res['面向']}｜要点 {res['要点数']}·配方 {res['配方数']}·来源 {res['来源数']}"
            f"·{res['耗时秒']}s" + ("｜待重试" if res.get("retry") else "") + (f"｜{res['error']}" if res.get("error") else ""))

    if args.json:
        cli.print_json({"结果": results})
        return 0

    report = DATA / f"学习-外网-{time.strftime('%Y%m%d-%H%M')}.md"
    mem = open_memory()
    lines = [f"# 外网学习记录（{len([r for r in results if r['要点数']])}/{len(results)} 条有产出）", ""]
    for res in results:
        fact = mem.fact(f"web:{res['主题']}") or {}
        lines.append(f"## {res['主题']}（{res['面向']}）")
        lines.append("")
        for point in (fact.get("要点") or [])[:10]:
            text = point.get("要点") if isinstance(point, dict) else str(point)
            src = (point.get("来源") if isinstance(point, dict) else "") or ""
            lines.append(f"- {text}" + (f"　[{src}]" if src else ""))
        for url in (res.get("来源") or [])[:6]:
            lines.append(f"- 来源：{url}")
        lines.append("")
        lines.append(f"（配方 {res['配方数']} 条已入记忆库，待排练；用时 {res['耗时秒']}s）")
        lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n本次学了 {len(results)} 条，报告：{report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
