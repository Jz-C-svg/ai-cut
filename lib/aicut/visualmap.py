"""视觉智能适配引擎 · 语义→视觉映射规则库（v3.7.0）。

纯函数、确定性（正则 + 词典），不调任何外部模型。输入转写句段与可选的语义档案，
输出一份"视觉计划"：每句话该叠什么（数字花字 / 警示标签 / 动作图标 / 结论卡 /
钩子大标题 / 情绪贴纸 / 关键词高亮）、叠在哪个位置、用哪个素材、什么样式。

设计要点：
  * 一句话可能命中多条规则，按优先级取最高一条；density=high 时允许叠加两条。
  * 中文数字 -> 阿拉伯数字（两到三天 -> 2-3天；两周 -> 14天）。
  * 钩子句由语义档案"高光候选"时间点覆盖的句子决定。
  * plan() 返回 list[dict]，并把统计挂在 list._stats 上；plan_with_stats() 同取。
"""
from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# 配色（母婴温馨调）
# --------------------------------------------------------------------------- #
C_ORANGE = "#FF9F43"   # 暖橙
C_PINK = "#FF6B81"     # 柔粉（警示/情绪）
C_BLUE = "#54A0FF"     # 天蓝
C_YELLOW = "#FECA57"  # 鹅黄
C_GREEN = "#1DD1A1"    # 草绿

# --------------------------------------------------------------------------- #
# 词典（母婴场景）
# --------------------------------------------------------------------------- #
# 症状 / 医学术语（含常见口播错别字）
SYMPTOM_WORDS = [
    "黄疸指数", "胆红素", "黄疸", "黄胆", "病理性", "母乳性", "生理性", "胜利性",
    "发烧", "发热", "湿疹", "红臀", "尿布疹", "脐带", "腹胀", "吐奶", "呛奶",
    "肺炎", "感染", "发烧", "皮疹", "腹泻", "便秘",
]
# 护理动作 -> 对应图标
ACTION_MAP: list[tuple[str, str]] = [
    ("拍嗝", "icon_burp.png"),
    ("喂奶", "icon_feeding.png"),
    ("哺乳", "icon_feeding.png"),
    ("洗澡", "icon_bath.png"),
    ("沐浴", "icon_bath.png"),
    ("换尿布", "icon_diaper.png"),
    ("尿布", "icon_diaper.png"),
    ("臀部护理", "icon_diaper.png"),
    ("脐带护理", "icon_baby.png"),
    ("抚触", "icon_baby.png"),
    ("按摩", "icon_baby.png"),
    ("清洁", "icon_baby.png"),
    ("消毒", "icon_medical.png"),
    ("保温", "icon_baby.png"),
    ("包裹", "icon_baby.png"),
    ("哄睡", "icon_moon.png"),
    ("安抚", "icon_heart.png"),
]
# 结论 / 警示句（只保留可执行的提醒/命令，避免描述性词抢了数字的优先级）
CONCLUSION_WORDS = [
    "需要及时就医", "及时就医", "一定要", "必须", "需要", "建议", "注意",
    "不要", "不能", "不准", "危险", "严重", "尽快", "立刻", "马上", "要就医",
]
# 情绪句
EMOTION_WORDS = [
    "开心", "高兴", "温暖", "幸福", "辛苦", "心疼", "焦虑", "担心",
    "害怕", "紧张", "放心", "安心",
]
# 症状里"偏重"的词 -> 用红色警示三角，其余用蓝色医疗标
SEVERE_WORDS = {"病理性", "肺炎", "感染", "危险", "发烧", "发热", "严重"}

# 数字单位（与任务书一致）
_UNITS = "天|日|周|个月|小时|分钟|秒|岁|斤|克|毫升|ml|ML|℃|度|次"

# 中文数字字符
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(s: str) -> int | None:
    """把一个简短中文数字串转成 int（覆盖月子口播常见小数字）。"""
    s = s.strip()
    if not s:
        return None
    # 纯阿拉伯
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return int(float(s))
    # 单字
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    # 十 / 十X / X十 / X十Y
    if s.startswith("十"):
        return 10 + (_CN_DIGIT.get(s[1:], 0) if len(s) > 1 else 0)
    if "十" in s:
        a, _, b = s.partition("十")
        tens = _CN_DIGIT.get(a, 1)
        ones = _CN_DIGIT.get(b, 0) if b else 0
        return tens * 10 + ones
    # 逐字（两两三三 之类不常见，逐字累乘无意义，直接映射）
    vals = [_CN_DIGIT.get(ch) for ch in s]
    if all(v is not None for v in vals):
        # 多位并列（如 三四 -> 取首位，range 由外层单独处理）
        return vals[0]
    return None


# 数字匹配：允许阿拉伯或中文数字，支持 "X到Y" / "X-Y" 区间
_CN_NUM = r"(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十]{1,3})"
_NUM_RANGE_RE = re.compile(
    rf"({_CN_NUM})\s*(?:到|至|~|～|-|—|–|--)\s*({_CN_NUM})\s*({_UNITS})")
_NUM_SINGLE_RE = re.compile(rf"({_CN_NUM})\s*({_UNITS})")


def _norm_number_text(raw: str) -> str:
    """把匹配到的中文数字片段规范化为阿拉伯字符串。"""
    v = cn_to_int(raw)
    return str(v) if v is not None else raw


def find_number(text: str) -> tuple[bool, str, str]:
    """在一句台词里找数字/时间。返回 (命中, 展示内容, 理由)。"""
    # 先尝试区间
    m = _NUM_RANGE_RE.search(text)
    if m:
        a, b, unit = m.group(1), m.group(2), m.group(3)
        na, nb = _norm_number_text(a), _norm_number_text(b)
        # 周 -> 天（两周 -> 14天）
        if unit == "周":
            try:
                content = f"{int(float(na)) * 7}-{int(float(nb)) * 7}天"
                return True, content, f"台词含时间区间'{m.group(0).strip()}'→数字放大花字"
            except ValueError:
                pass
        content = f"{na}-{nb}{unit}"
        return True, content, f"台词含数字区间'{m.group(0).strip()}'→数字放大花字"
    m = _NUM_SINGLE_RE.search(text)
    if m:
        n, unit = m.group(1), m.group(2)
        nstr = _norm_number_text(n)
        if unit == "周":
            try:
                nstr = str(int(float(nstr)) * 7)
                unit = "天"
            except ValueError:
                pass
        content = f"{nstr}{unit}"
        return True, content, f"台词含数字'{m.group(0).strip()}'→数字放大花字"
    return False, "", ""


def find_symptom(text: str) -> tuple[bool, str, str, str]:
    hits = [w for w in SYMPTOM_WORDS if w in text]
    if not hits:
        return False, "", "", ""
    severe = [w for w in hits if w in SEVERE_WORDS]
    w = severe[0] if severe else hits[0]
    asset = "icon_warning.png" if w in SEVERE_WORDS else "icon_medical.png"
    disp = "黄疸" if w in ("黄胆", "胜利性", "生理性") else w
    return True, disp, asset, f"台词含医学/症状词'{w}'→警示标签"


def find_action(text: str) -> tuple[bool, str, str, str]:
    for w, asset in ACTION_MAP:
        if w in text:
            return True, w, asset, f"台词含护理动作'{w}'→动作图标"
    return False, "", "", ""


def find_conclusion(text: str) -> tuple[bool, str]:
    for w in CONCLUSION_WORDS:
        if w in text:
            return True, f"台词含结论/警示词'{w}'→结论卡"
    return False, ""


def find_emotion(text: str) -> tuple[bool, str]:
    for w in EMOTION_WORDS:
        if w in text:
            asset = "icon_exclaim.png" if w in ("担心", "害怕", "紧张", "焦虑") else "icon_heart.png"
            return True, asset
    return False, ""


def extract_keyword(text: str) -> str:
    """普通句取一个关键词高亮：挑最长的中文名词短语片段（2-6 字）。"""
    # 去标点后按最长连续中文片段切
    chunks = re.findall(r"[一-龥A-Za-z]{2,8}", text)
    if not chunks:
        return ""
    # 去掉常见虚词
    stop = {"我们", "你们", "这个", "那个", "就是", "可以", "一般", "情况", "一下", "什么", "为什么"}
    best = ""
    for c in chunks:
        if c in stop:
            continue
        if len(c) > len(best):
            best = c
    return best[:6]


# --------------------------------------------------------------------------- #
# 各类型默认样式
# --------------------------------------------------------------------------- #
def _style(number: int, color: str, bg: str | None, bold: bool) -> dict:
    return {"font_size": number, "color": color, "bg_color": bg, "bold": bold}


_TYPE_POSITION = {
    "number_card": "top_right",
    "warning_label": "top_left",
    "action_icon": "top_right",
    "conclusion_card": "top_center",
    "hook_title": "top_center",
    "emotion_sticker": "center",
    "keyword_highlight": "bottom_right",
}

# 优先级：数字越大越优先
_PRIORITY = {
    "hook_title": 7,
    "conclusion_card": 6,
    "number_card": 5,
    "warning_label": 4,
    "action_icon": 3,
    "emotion_sticker": 2,
    "keyword_highlight": 1,
}

# 密度开关：哪些类型允许出现
_DENSITY_ALLOW = {
    "low": {"hook_title", "conclusion_card", "number_card"},
    "medium": {"hook_title", "conclusion_card", "number_card",
               "warning_label", "action_icon", "emotion_sticker"},
    "high": {"hook_title", "conclusion_card", "number_card",
             "warning_label", "action_icon", "emotion_sticker", "keyword_highlight"},
}


def _make_item(t0: float, t1: float, vtype: str, content: str, asset: str | None,
               style: dict, reason: str, source_text: str, confidence: float) -> dict:
    return {
        "t0": round(float(t0), 3),
        "t1": round(float(t1), 3),
        "type": vtype,
        "content": content,
        "asset": asset,
        "position": _TYPE_POSITION[vtype],
        "style": style,
        "reason": reason,
        "source_text": source_text,
        "confidence": round(confidence, 2),
    }


def _sentence_rules(text: str) -> list[dict]:
    """对单句跑全部规则，返回按优先级降序的候选列表。"""
    cands: list[dict] = []

    ok, ncontent, nreason = find_number(text)
    if ok:
        asset = "icon_clock.png" if any(u in ncontent for u in ("天", "周", "小时", "分钟", "秒", "岁")) \
            else "icon_number.png"
        cands.append(_make_item(
            0, 0, "number_card", ncontent, asset,
            _style(9.5, C_ORANGE, C_YELLOW, True), nreason, text, 0.9))

    ok, sym, asset, sreason = find_symptom(text)
    if ok:
        cands.append(_make_item(
            0, 0, "warning_label", sym, asset,
            _style(8.5, C_PINK, None, True), sreason, text, 0.85))

    ok, act, aasset, areason = find_action(text)
    if ok:
        cands.append(_make_item(
            0, 0, "action_icon", act, aasset,
            _style(8.0, C_BLUE, None, False), areason, text, 0.8))

    ok, creason = find_conclusion(text)
    if ok:
        cands.append(_make_item(
            0, 0, "conclusion_card", "重点", "card_warning.png",
            _style(9.5, "#FFFFFF", C_PINK, True), creason, text, 0.85))

    ok, easset = find_emotion(text)
    if ok:
        cands.append(_make_item(
            0, 0, "emotion_sticker", "", easset,
            _style(8.0, C_PINK, None, True), "台词含情绪词→情绪贴纸", text, 0.7))

    kw = extract_keyword(text)
    if kw:
        cands.append(_make_item(
            0, 0, "keyword_highlight", kw, None,
            _style(7.0, C_BLUE, None, True), "普通句提取关键词高亮", text, 0.5))

    cands.sort(key=lambda c: _PRIORITY[c["type"]], reverse=True)
    return cands


def _hook_segments(segments: list[dict], semantic_doc: dict | None) -> set[int]:
    """语义档案高光候选时间点覆盖到的句子下标集合。"""
    idx: set[int] = set()
    if not semantic_doc:
        return idx
    for h in (semantic_doc.get("高光候选") or []):
        try:
            ht = float(h.get("t", -1))
        except (TypeError, ValueError):
            continue
        best_i, best_d = -1, 1e9
        # 优先严格落在某句区间内；边界点取区间起点最近的一句
        for i, seg in enumerate(segments):
            s0, s1 = float(seg["start"]), float(seg["end"])
            if s0 <= ht <= s1:
                d = abs(ht - s0)
                if d < best_d:
                    best_d, best_i = d, i
        # 没有严格落在任何一句内（高光点在句间空隙）才用 ±0.7s 兜底
        if best_i < 0:
            for i, seg in enumerate(segments):
                s0, s1 = float(seg["start"]), float(seg["end"])
                if s0 - 0.7 <= ht <= s1 + 0.7:
                    d = min(abs(ht - s0), abs(ht - s1))
                    if d < best_d:
                        best_d, best_i = d, i
        if best_i >= 0:
            idx.add(best_i)
    return idx


class VisualPlan(list):
    """视觉计划列表：在普通 list 上附带命中统计 _stats。"""

    _stats: dict[str, Any]

    def __new__(cls, items: list[dict] | None = None, stats: dict | None = None) -> "VisualPlan":
        obj = super().__new__(cls)
        obj._stats = stats or {}
        return obj

    def __init__(self, items: list[dict] | None = None, stats: dict | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(items or [])
        self._stats = stats or {}


def plan(transcript_segments: list[dict], semantic_doc: dict | None = None,
         density: str = "medium") -> VisualPlan:
    """生成视觉计划。返回 list[dict]，并在 list._stats 上挂命中统计。"""
    allow = _DENSITY_ALLOW.get(density, _DENSITY_ALLOW["medium"])
    hooks = _hook_segments(transcript_segments, semantic_doc)

    out: list[dict] = []
    by_type: dict[str, int] = {}
    hit_sentences = 0

    for i, seg in enumerate(transcript_segments):
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        t0, t1 = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        if t1 - t0 < 0.2:
            continue

        cands = _sentence_rules(text)
        # 钩子句：直接强制 hook_title（最高优先级），保留原句做超大标题
        is_hook = i in hooks
        chosen: list[dict] = []
        if is_hook:
            chosen.append(_make_item(
                t0, t1, "hook_title", text[:10], None,
                _style(14.0, C_YELLOW, C_ORANGE, True),
                "语义档案高光候选覆盖→钩子大标题", text, 0.95))

        # 从允许类型里挑
        pool = [c for c in cands if c["type"] in allow]
        if pool:
            top = pool[0]
            top["t0"], top["t1"] = t0, t1
            chosen.append(top)
            # density=high 时叠加第二条不同类型
            if density == "high" and len(pool) >= 2 and len(chosen) < 2:
                second = pool[1]
                second["t0"], second["t1"] = t0, t1
                chosen.append(second)

        if chosen:
            hit_sentences += 1
        for item in chosen:
            out.append(item)
            by_type[item["type"]] = by_type.get(item["type"], 0) + 1

    stats = {
        "总句数": len(transcript_segments),
        "命中句数": hit_sentences,
        "各类型计数": by_type,
        "密度": density,
        "计划总数": len(out),
    }
    return VisualPlan(out, stats)


def plan_with_stats(transcript_segments: list[dict], semantic_doc: dict | None = None,
                    density: str = "medium") -> tuple[list[dict], dict]:
    """与 plan() 相同，但显式返回 (plan, stats)。"""
    p = plan(transcript_segments, semantic_doc, density)
    return p, getattr(p, "_stats", {})


if __name__ == "__main__":  # 自测快查
    demo = [
        {"start": 0.0, "end": 1.8, "text": "这就是黄疸"},
        {"start": 1.8, "end": 5.6, "text": "正常的生理性黄疸是两到三天出现"},
        {"start": 5.6, "end": 9.2, "text": "五到七天是一个高峰期，需要注意"},
        {"start": 9.2, "end": 12.8, "text": "一般情况下在两周之内它可以消退"},
    ]
    doc = {"高光候选": [{"t": 1.8, "理由": "起始时间点"}, {"t": 5.6, "理由": "高峰期"}]}
    p, s = plan_with_stats(demo, doc, "medium")
    for it in p:
        print(it["type"], it["content"], it["position"], it["style"]["font_size"])
    print(s)
