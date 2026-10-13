#!/usr/bin/env python3
r"""复盘环：成片发布后，把「决策单 + 评分 + 后台数据」提炼成「下次怎么改」，
写入案例库与语义记忆，供下次规划时 few-shot 召回。

用法:
  python retrospect.py --sheet "03-scripts/月子知识-成片.sheet.json" --score 86
  python retrospect.py --sheet "..." --data "08-data/后台导出.csv" --note "钩子偏弱"
  python retrospect.py --import-cases        # 一次性把历史工作区导入案例库
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from pathlib import Path

if __name__ == '__main__' and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import case_library as cl  # noqa: E402
from aicut import memory_semantic as ms  # noqa: E402


def distill_lessons(sheet: dict, score: float | None, data_note: str, provider: str) -> str:
    """LLM 生成 1-3 条复盘要点；不可用时返回规则摘要。"""
    clips = sheet.get("clips") or []
    fact = (f"工程={sheet.get('project')} 风格={sheet.get('style') or sheet.get('template')} "
            f"片段数={len(clips)} 封面={sheet.get('cover_text')} CTA={sheet.get('cta')} "
            f"评分={score} 数据观察={data_note or '无'}")
    try:
        import understand  # noqa: E402
        data, _ = understand.llm_json(
            "你是短视频复盘助手。根据以下成片事实，输出 JSON："
            '{"复盘": ["下次要改的1-3条具体做法"]}。要具体可执行，不要空话。\n' + fact, provider)
        if data and data.get("复盘"):
            return "；".join(str(x) for x in data["复盘"][:3])
    except Exception:
        pass
    # 规则降级
    rules = []
    if score is not None and score < 70:
        rules.append(f"评分仅 {score}，优先排查钩子（前 3 秒）与落点")
    if len(clips) > 12:
        rules.append(f"片段数 {len(clips)} 偏碎，下次放宽最短镜头时长")
    if not data_note:
        rules.append("缺后台数据观察，发布 48h 后补录留存数据再复盘")
    return "；".join(rules) or "本条无明显改进点"


def main() -> int:
    ap = argparse.ArgumentParser(description="成片复盘 → 案例库 + 语义记忆")
    ap.add_argument("--sheet", help="决策单 JSON 路径")
    ap.add_argument("--score", type=float, help="发布前 evaluate 六维分")
    ap.add_argument("--data", help="后台导出 csv/xlsx（可附观察结论）")
    ap.add_argument("--note", help="人工观察备注")
    ap.add_argument('--match-column', help='后台数据中用于匹配作品的列')
    ap.add_argument('--match-value', help='后台数据作品标题/ID；默认使用工程名')
    ap.add_argument("--provider", default="auto", choices=["auto", "deepseek", "ollama"])
    ap.add_argument("--import-cases", action="store_true", help="把历史工作区导入案例库")
    args = ap.parse_args()

    if args.import_cases:
        n = cl.import_from_workspace()
        print(f"[导入] 案例库现有 {ms.count(kind='case')} 条（本次新增 {n}）")
        return 0

    if not args.sheet:
        ap.error("需要 --sheet（或 --import-cases）")
    if args.score is not None and (not math.isfinite(args.score) or not 0 <= args.score <= 100):
        ap.error('评分必须在 0–100 之间')
    sheet = json.loads(Path(args.sheet).read_text(encoding="utf-8-sig"))
    name = sheet.get("project") or Path(args.sheet).stem

    from aicut.feedback import load_feedback
    from aicut.production import atomic_json
    post_data = {'note': args.note} if args.note else {}
    if args.data:
        post_data['metrics'] = load_feedback(args.data, name, args.match_column, args.match_value)
    observation = json.dumps(post_data, ensure_ascii=False) if post_data else ''
    lessons = distill_lessons(sheet, args.score, observation, args.provider)
    key = cl.add_case(name, sheet, score=args.score,
                      post_data=post_data or None,
                      lessons=lessons)
    print(f"[复盘] {name} → {key}")
    print(f"[要点] {lessons}")
    # 同时写一条语义条目，规划时可按目标语义召回
    ms.upsert(f"lesson:{name}", f"{name} 复盘要点：{lessons}", kind="lesson",
              meta={"sheet": args.sheet, "score": args.score})
    import uuid
    evidence = ROOT / '08-data' / 'feedback' / (uuid.uuid4().hex + '.json')
    atomic_json(evidence, {'project': name, 'sheet': args.sheet, 'score': args.score,
                           'observations': post_data, 'lessons': lessons, 'case_key': key})
    print(f'[复盘证据] {evidence}')
    print(f"[索引] 语义库共 {ms.count()} 条；下次 agent 规划会自动召回本条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
