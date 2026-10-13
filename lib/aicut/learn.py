r"""学习环：跟你的历史成片学偏好，跟网上的公开资料学做法。

三个来源（设计里的"源 1/2/3"）在本模块里落地为：
  mine_from_workspace()  从你自己的决策单与草稿里挖出剪辑习惯（离线、可核验）
  web_learn(topic)       联网检索 -> 结构化做法与界面配方 -> 进记忆库
  apply_preferences()    把挖出来的习惯写进 memory.sqlite，规划器每轮强制读

原则：学的是"做法与习惯"，不是别人的表达；每条都留下证据（哪个文件、哪条链接）。
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

from . import llm
from .draft import Draft
from .memory import Memory, open_memory
from .paths import DATA, PROJECT_DRAFTS, SCRIPTS, SHEETS


def _sheet_files() -> list[Path]:
    return sorted(SHEETS.glob("*.sheet.json"))


def _draft_dirs() -> list[Path]:
    if not PROJECT_DRAFTS.exists():
        return []
    return sorted(d for d in PROJECT_DRAFTS.iterdir()
                  if d.is_dir() and (d / "draft_content.json").exists())


def mine_from_workspace() -> dict:
    """统计你自己的成片习惯：镜头长度、字幕密度、包装用法。"""
    shot_lengths: list[float] = []
    text_lengths: list[int] = []
    text_lines: list[float] = []
    transitions: list[str] = []
    filters: list[str] = []
    effects: list[str] = []
    styles: list[str] = []
    covers: list[int] = []
    with_bgm = 0
    drafts = _draft_dirs()

    for d in drafts:
        try:
            draft = Draft.load(d)
        except Exception:  # noqa: BLE001
            continue
        shot_lengths += [s.length for s in draft.video_segments if s.length > 0.2]
        for seg in draft.text_segments:
            text = (seg.text or "").strip()
            if text:
                text_lengths.append(len(text))
        for mat in draft.materials_of("texts"):
            try:
                meta = json.loads(mat.get("content") or "{}")
                if meta.get("line_max_width"):
                    text_lines.append(float(meta["line_max_width"]))
            except ValueError:
                pass
        for mat in draft.materials_of("transitions"):
            name = mat.get("name") or ""
            if name:
                transitions.append(name)
        for mat in draft.materials_of("filters"):
            name = mat.get("name") or ""
            if name:
                filters.append(name)
        for mat in draft.materials_of("video_effects") + draft.materials_of("effects"):
            name = mat.get("name") or ""
            if name:
                effects.append(name)
        if draft.audio_segments or draft.materials_of("audios"):
            with_bgm += 1
        # 封面文案：时长 > 视频总长一半的文本段视为封面
        for seg in draft.text_segments:
            if seg.t <= 0.05 and seg.length >= 1.2 and (seg.text or ""):
                covers.append(len(seg.text))

    for f in _sheet_files():
        try:
            sheet = json.loads(f.read_text(encoding="utf-8-sig"))
        except ValueError:
            continue
        if sheet.get("style"):
            styles.append(str(sheet["style"]))
        for clip in sheet.get("clips") or []:
            if clip.get("sfx"):
                pass

    return {
        "草稿数": len(drafts),
        "决策单数": len(_sheet_files()),
        "镜头": {
            "样本": len(shot_lengths),
            "中位时长": round(statistics.median(shot_lengths), 3) if shot_lengths else None,
            "最短": round(min(shot_lengths), 3) if shot_lengths else None,
            "最长": round(max(shot_lengths), 3) if shot_lengths else None,
        },
        "字幕": {
            "样本": len(text_lengths),
            "中位字数": int(statistics.median(text_lengths)) if text_lengths else None,
            "最长字数": max(text_lengths) if text_lengths else None,
            "中位行宽": round(statistics.median(text_lines), 3) if text_lines else None,
        },
        "封面字数": {"样本": len(covers), "中位": int(statistics.median(covers)) if covers else None},
        "转场": sorted(set(transitions)),
        "滤镜": sorted(set(filters)),
        "特效": sorted(set(effects)),
        "文字样式": sorted(set(styles)),
        "带BGM草稿": with_bgm,
    }


def preferences_from(stats: dict) -> list[dict]:
    """把统计变成能被执行、也能被反驳的规则。"""
    out: list[dict] = []

    def conf(samples: int, base: float = 0.45, step: float = 0.08, cap: float = 0.9) -> float:
        return round(min(cap, base + step * max(samples, 0)), 2)

    shot = stats.get("镜头") or {}
    if shot.get("中位时长"):
        med = shot["中位时长"]
        out.append({
            "rule": f"镜头时长尽量落在 {med:.1f}s ± 0.8s（你的历史成片中位值）",
            "evidence": f"{shot['样本']} 个历史片段",
            "confidence": conf(shot.get("样本", 0)),
        })
    text = stats.get("字幕") or {}
    if text.get("中位字数"):
        limit = int(text["中位字数"]) + 2
        out.append({
            "rule": f"字幕尽量不超过 {limit} 个字",
            "evidence": f"{text['样本']} 条历史字幕，中位 {text['中位字数']} 字",
            "confidence": conf(text.get("样本", 0)),
        })
    if text.get("中位行宽"):
        out.append({
            "rule": f"字幕行宽保持在你惯用的 {text['中位行宽']:.2f} 左右",
            "evidence": "历史草稿里的 line_max_width",
            "confidence": 0.6,
        })
    cover = stats.get("封面字数") or {}
    if cover.get("中位"):
        out.append({
            "rule": f"每条都配封面文案，长度约 {cover['中位']} 字",
            "evidence": f"{cover['样本']} 条历史成片",
            "confidence": conf(cover.get("样本", 0), base=0.5),
        })
    if stats.get("转场"):
        out.append({
            "rule": "优先使用你惯用的转场：" + "、".join(stats["转场"][:4]),
            "evidence": "历史草稿的转场素材",
            "confidence": 0.6,
        })
    if stats.get("文字样式"):
        out.append({
            "rule": "字幕文字样式优先：" + "、".join(stats["文字样式"][:4]),
            "evidence": "历史决策单",
            "confidence": 0.65,
        })
    if stats.get("草稿数") and stats.get("带BGM草稿", 0) == 0:
        out.append({
            "rule": "历史成片都没配 BGM：没特别要求时先不加背景音乐",
            "evidence": f"{stats['草稿数']} 个历史草稿",
            "confidence": 0.55,
        })
    return out


def apply_preferences(*, mem: Memory | None = None, stats: dict | None = None,
                      source: str = "workspace") -> dict:
    mem = mem or open_memory()
    stats = stats or mine_from_workspace()
    rules = preferences_from(stats)
    for item in rules:
        # 同一主题只留最新一条（阈值/中位数会随样本变化）
        mem.db.execute("DELETE FROM preferences WHERE source=? AND substr(rule,1,8)=?",
                       (source, item["rule"][:8]))
        mem.db.commit()
        mem.add_preference(item["rule"], evidence=item["evidence"],
                           confidence=item["confidence"], source=source)
    mem.set_fact("workspace_stats", stats, source=source)
    return {"stats": stats, "rules": rules}


def report_markdown(*, stats: dict | None = None, rules: list[dict] | None = None) -> str:
    stats = stats or mine_from_workspace()
    rules = rules or preferences_from(stats)
    lines = ["# 学习你的剪辑习惯（自动生成）", "",
             f"样本：{stats.get('草稿数')} 个草稿、{stats.get('决策单数')} 份决策单", ""]
    lines.append("## 统计数据")
    for key, value in stats.items():
        lines.append(f"- {key}：{json.dumps(value, ensure_ascii=False)}")
    lines += ["", "## 提炼出的偏好规则", ""]
    for item in rules:
        lines.append(f"- （置信度 {item['confidence']:.0%}）{item['rule']}　← {item['evidence']}")
    lines += ["", "> 这些规则会作为硬约束进入规划器；想改就直接改 memory.sqlite 里的 preferences，"
                  "或删掉重新跑一次 learn_from_demo.py。", ""]
    return "\n".join(lines)


def search_web(prompt: str, tokens: int = 2500) -> dict:
    """复用 research.py 的联网检索（Responses API + web_search）。"""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import research  # noqa: PLC0415

    key = research.load_key()
    if not key:
        return _public_search(prompt)
    # 说明：检索型提问一旦很长，模型会把预算全花在检索上而不输出结论（实测两次空手而归）。
    # 对策：限制检索次数；仍然没结论时，用短提问重试一次。
    def _ask(text: str, budget: int, calls: int) -> dict:
        import json as _json
        import urllib.request as _url
        payload = {"model": research.MODEL, "input": text,
                   "tools": [{"type": "web_search"}],
                   "max_output_tokens": budget, "max_tool_calls": calls}
        req = _url.Request(research.URL, data=_json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json",
                                   "Authorization": "Bearer " + key})
        return _json.loads(_url.urlopen(req, timeout=600).read().decode("utf-8"))

    try:
        out = research.parse(_ask(prompt, tokens, 3))
    except Exception as exc:
        fallback = _public_search(prompt)
        fallback["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return fallback
    if not out.get("回答"):
        short = prompt[:120]
        out = research.parse(_ask(short, max(tokens, 3000), 2))
    return out


def _public_search(prompt: str) -> dict:
    """API-free fallback using the machine's normal network connection."""
    from . import web_research
    found = web_research.search_all(prompt, lang="both", max_results=12)
    lines = []
    sources = []
    for item in found.get("snippets") or []:
        text = str(item.get("text") or "").strip()
        url = str(item.get("url") or "").strip()
        if text:
            lines.append(f"- {text}")
        if url and url not in sources:
            sources.append(url)
    return {"查询": prompt, "回答": "\n".join(lines), "来源": sources,
            "原文": "\n".join(lines), "channels": found.get("channels", [])}


DISTILL_PROMPT = """下面是一段联网检索到的公开资料，主题是「{topic}」。
请把它整理成结构化的做法，只保留可操作的内容，不要抄写原文长句，不要输出解释。
只输出 JSON：
{{
  "要点": [{{"要点": "一句话可操作的结论", "来源": "对应链接或站点名"}}],
  "界面配方": [
    {{"目标": "实现什么效果", "前置条件": "需要什么素材或状态",
      "步骤": [{{"动作": "点哪里/选什么/填什么数值", "参数": "具体数值或范围"}}],
      "来源": "链接"}}
  ]
}}
资料：
"""


def web_learn(topic: str, *, mem: Memory | None = None, tokens: int = 2500,
              provider: str = "auto") -> dict:
    mem = mem or open_memory()
    question = (f"搜索并总结：在剪映专业版（Windows）里，{topic} 具体怎么做？"
                f"给出可操作步骤、关键参数数值范围和常见坑，附来源链接。")
    found = search_web(question, tokens)
    answer = found.get("回答") or ""
    sources = sources_of(found, answer)
    structured: dict = {}
    if answer:
        try:
            structured = llm.json_chat(
                [{"role": "system", "content": "你是剪辑资料整理员，只输出 JSON。"},
                 {"role": "user", "content": DISTILL_PROMPT.format(topic=topic) + answer[:12000]}],
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001
            structured = {"要点": [{"要点": "整理失败，原文见 facts", "来源": ""}],
                          "界面配方": [], "error": str(exc)[:200]}
    recipes = structured.get("界面配方") or []
    mem.set_fact(f"web:{topic}", {"查询": found.get("查询"), "来源": sources,
                                  "要点": _points_of(structured, answer),
                                  "原文": answer[:6000]}, source="web")
    for i, recipe in enumerate(recipes):
        sid = "web-" + re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", topic)[:20] + f"-{i:02d}"
        mem.upsert_skill({
            "id": sid,
            "goal": recipe.get("目标") or topic,
            "channel": "ui",
            "preconditions": [recipe.get("前置条件")] if recipe.get("前置条件") else [],
            "steps": recipe.get("步骤") or [],
            "verify": {"type": "screen_changed", "note": "界面配方需在剪映里试跑后才可信"},
            "source": "web",
            "source_url": recipe.get("来源") or (sources[0] if sources else ""),
            "success": 0.0, "runs": 0, "fails": 0,
        })
    return {"topic": topic, "要点数": len(_points_of(structured, answer)),
            "配方数": len(recipes), "来源数": len(sources), "来源": sources[:8],
            "原文长度": len(answer)}


def knowledge(limit: int = 12) -> list[dict]:
    """取出联网学到的要点（facts 里 web: 开头的内容），供规划器参考。"""
    mem = open_memory()
    out: list[dict] = []
    for key, value in (mem.facts() or {}).items():
        if not key.startswith("web:"):
            continue
        data = value if isinstance(value, dict) else {}
        points = data.get("要点") or []
        for point in points:
            text = point.get("要点") if isinstance(point, dict) else str(point)
            if not text:
                continue
            out.append({
                "主题": key[4:],
                "要点": text,
                "来源": (point.get("来源") if isinstance(point, dict) else "") or "",
            })
    return out[:limit]


def recipes(order: str = "success DESC") -> list[dict]:
    """取出联网学到的界面配方（channel=ui 且 source=web）。"""
    mem = open_memory()
    out = []
    for skill in mem.skills(order):
        if skill.get("source") == "web" and skill.get("channel") == "ui":
            out.append({
                "id": skill["id"],
                "目标": skill.get("goal") or "",
                "步骤数": len(skill.get("steps") or []),
                "成功率": skill.get("success") or 0.0,
                "跑过几次": skill.get("runs") or 0,
                "来源": skill.get("source_url") or "",
                "步骤": skill.get("steps") or [],
                "前置条件": skill.get("preconditions") or [],
            })
    return out


def knowledge_brief(limit: int = 12, goal: str = "") -> list[str]:
    """给规划器看的一行行"参考做法"。"""
    items = knowledge(max(limit * 3, 24))
    def score(item: dict) -> int:
        text = str(item.get("主题", "")) + str(item.get("要点", ""))
        return sum(1 for ch in set(str(goal or "")) if ch in text)
    items.sort(key=score, reverse=True)
    return [f"{item['主题']}：{item['要点']}" for item in items[:limit]]


def prune_recordings(keep: int = 60) -> dict:
    """录制证据只留最近 keep 份，避免 work/demos 无限增长。"""
    import shutil as _shutil

    from .paths import WORK

    base = WORK / "demos"
    if not base.exists():
        return {"removed": 0, "kept": 0}
    dirs = sorted([d for d in base.iterdir() if d.is_dir()])
    removed = 0
    for d in dirs[:-keep] if len(dirs) > keep else []:
        _shutil.rmtree(d, ignore_errors=True)
        removed += 1
    return {"removed": removed, "kept": min(len(dirs), keep)}


SOURCE_HINT = {
    "wiki": ("优先读 Wikipedia（中文或英文条目均可）：先给条目名，再归纳可直接用于短视频剪辑的要点，"
             "每条要点后面跟来源链接。"),
    "github": ("优先读 GitHub 仓库与它的 README/文档：说清这个项目解决什么问题、关键参数怎么调、"
               "和我们的做法有什么差别，并给仓库链接。"),
    "paper": ("优先读公开论文与综述（arXiv、期刊页面可访问的那部分）：给结论、常用指标与阈值，"
              "并给论文或页面链接。"),
    "doc": ("只采信官方或权威机构的公开文档（平台官方规范、行业标准组织、知名制作方规范），"
            "给条款要点与来源链接；找不到权威来源就直说。"),
    "general": "综合公开资料，给可操作要点与来源链接。",
}


def build_question(entry: dict) -> str:
    """按主题的"面向"生成检索提问。短句 + 明确要什么，避免模型把预算全花在检索上。"""
    hint = SOURCE_HINT.get(str(entry.get("面向") or "general"), SOURCE_HINT["general"])
    return (f"检索：{entry.get('检索词')}。"
            f"{hint}"
            f"最后给我 5-8 条可直接落地的要点，每条一句话，不要长篇解释。")


def web_learn_custom(topic: str, question: str, *, tokens: int = 3000, provider: str = "auto",
                     mem: Memory | None = None, want_recipes: bool = True) -> dict:
    """按自定义提问学一个主题，结果进 facts（要点+来源）与 skills（界面配方）。

    与 web_learn 的区别只在提问与是否整理配方；存储路径保持一致，便于统一检索与瘦身。
    """
    mem = mem or open_memory()
    found = search_web(question, tokens)
    answer = found.get("回答") or ""
    sources = sources_of(found, answer)
    structured: dict = {}
    if answer:
        try:
            structured = llm.json_chat(
                [{"role": "system", "content": "你是剪辑资料整理员，只输出 JSON。"},
                 {"role": "user", "content": DISTILL_PROMPT.format(topic=topic) + answer[:12000]}],
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001
            structured = {"要点": [], "界面配方": [], "error": str(exc)[:200]}
    recipes = (structured.get("界面配方") or []) if want_recipes else []
    mem.set_fact(f"web:{topic}", {
        "查询": found.get("查询"),
        "来源": sources,
        "要点": _points_of(structured, answer),
        "原文": answer[:6000],
        "提问": question[:300],
    }, source="web")
    written = 0
    for i, recipe in enumerate(recipes):
        sid = "web-" + re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", topic)[:22] + f"-{i:02d}"
        mem.upsert_skill({
            "id": sid,
            "goal": recipe.get("目标") or topic,
            "channel": "ui",
            "preconditions": [recipe.get("前置条件")] if recipe.get("前置条件") else [],
            "steps": recipe.get("步骤") or [],
            "verify": {"type": "screen_changed", "note": "界面配方需在剪映里试跑后才可信"},
            "source": "web",
            "source_url": recipe.get("来源") or (sources[0] if sources else ""),
            "success": 0.0, "runs": 0, "fails": 0,
        })
        written += 1
    return {"主题": topic, "面向": "?", "要点数": len(_points_of(structured, answer)),
            "配方数": written, "来源数": len(sources), "来源": sources[:6],
            "原文长度": len(answer), "error": structured.get("error", "")}


URL_RE = re.compile(r"https?://[^\s\)\]，。；、）\"']+")


def sources_of(found: dict, answer: str) -> list[str]:
    """来源 = 检索工具打开的页面 ∪ 正文里给出的链接（去重、保持顺序）。

    实测：模型经常把 Wikipedia 链接写在正文里而不触发 open_page，只取工具列表会得到空来源。
    """
    urls = [u for u in (found.get("来源") or []) if u]
    for raw in URL_RE.findall(answer or ""):
        url = raw.rstrip(".,;")
        if url not in urls:
            urls.append(url)
    return urls[:24]


SOURCE_HINT["en"] = ("优先英文来源（arXiv、GitHub 项目与 README、官方博客、Papers with Code、"
                     "Hugging Face、厂商文档），要点用中文写、来源保留原文链接；"
                     "找不到可靠来源就直说，不要编。")


BULLET_RE = re.compile(r"^\s*(?:\d+[\.、)）]|[-*·•—])\s*\S")


def _points_of(structured: dict, answer: str, limit: int = 24) -> list[dict]:
    """把"整理结果"里的要点取出来；取不到就从原文里救——检索花了钱，别浪费。

    实测有一类主题：原文正常（还带 8 个链接），但模型输出的 JSON 用了别的键名或干脆给空数组，
    结果被判"没学成"。这里依次尝试：要点 / 要点列表 / points / 结论，最后按原文的项目符号行兜底。
    """
    for key in ("要点", "要点列表", "points", "结论", "关键要点"):
        value = structured.get(key)
        if isinstance(value, list) and value:
            return value[:limit]
    picked: list[dict] = []
    for line in (answer or "").splitlines():
        text = line.strip()
        if BULLET_RE.match(text):
            text = re.sub(r"^\s*(?:\d+[\.、)）]|[-*·•—])\s*", "", text).strip()
            if 4 <= len(text) <= 200:
                picked.append({"要点": text, "来源": ""})
        if len(picked) >= limit:
            break
    if picked:
        return picked
    head = (answer or "").strip()
    return [{"要点": head[:300], "来源": ""}] if len(head) >= 20 else []
