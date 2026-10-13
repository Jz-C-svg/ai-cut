# -*- coding: utf-8 -*-
r"""平台校准引擎（v1.0）——基于平台评测库 JSON 档案，把平台规则/口味注入剪辑方案。

定位：
  内容架构（architecture）解决「讲什么结构」，风格（style）解决「怎么剪」，
  本模块解决「发到哪个平台、要遵守该平台什么硬规则与口味」——
  把 content-library/platform-benchmarks/*.json 里的校准参数，注入 edit_intent 字典，
  并对方案文本做母婴行业违禁词扫描（含安全替代词建议）。

硬约束（项目）：
  - 只用剪映（JianyingPro）成片，FFmpeg 仅分析；本模块不导出成片，只做方案层校准。
  - 免费本地优先：纯本地读 JSON，不调任何在线接口。
  - 默认平台 = douyin（抖音），不传 platform 时链路与旧行为完全一致。

用法：
    from aicut import platform_calibrator as pc
    pc.list_platforms()
    cal = pc.get_calibration("douyin")
    intent = pc.calibrate_intent(intent_dict, "douyin")
    diff = pc.compare_platforms("douyin", "bilibili", intent_dict)
    hits = pc.scan_banned_words("我们根治腹直肌", "douyin")
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .paths import CONTENT_LIBRARY

PLATFORM_DIR = CONTENT_LIBRARY / "platform-benchmarks"
_INDEX_FILE = PLATFORM_DIR / "platform-benchmarks.json"
DEFAULT_PLATFORM = "douyin"

# ───────────────────────── 母婴行业安全替代词表 ─────────────────────────
# 键：平台违禁/高风险表述；值：安全替代词。蓝V企业号（非医疗机构）不得做医疗承诺。
SAFE_REPLACEMENTS: dict[str, str] = {
    "治疗产后漏尿": "产后漏尿护理陪伴",
    "治疗月子病": "产后调养",
    "修复腹直肌分离": "腹直肌护理",
    "修复腹直肌": "腹直肌护理",
    "修复盆底肌": "盆底肌护理",
    "催乳治疗": "催乳指导",
    "消炎催乳": "温和催乳",
    "治疗": "护理",
    "治愈": "调养",
    "根治": "调养",
    "断根": "改善",
    "诊疗": "咨询",
    "药到病除": "循序渐进",
    "消炎": "舒缓",
    "杀菌": "清洁",
    "排毒": "代谢辅助",
    "增强免疫力": "帮助体质恢复",
    "无副作用": "温和安心",
    "零激素": "成分安心",
    "医学级": "专业级",
    "医用级": "专业级",
    "无效退款": "用心服务",
    "零风险": "安心",
    "安全无毒": "安心",
    "安全": "安心",
    "纯天然无添加": "成分自然",
    "替代母乳": "接近母乳营养",
    "媲美母乳": "营养均衡",
    "更接近母乳": "营养均衡",
    "赛过母乳": "营养均衡",
    "100%有效": "大幅改善",
    "100%": "大幅",
    "百分百": "大幅",
    "最佳": "非常好",
    "最好": "很好",
    "最高级": "优质",
    "国家级": "专业级",
    "顶级": "优质",
    "极致": "用心",
    "唯一": "难得",
    "第一": "领先",
    "绝对": "相当",
    "永久": "长期",
    "万能": "多用途",
    "速效": "高效",
    "保本": "稳健",
    "高收益": "稳健收益",
    "零风险投资": "稳健规划",
}

# 单字/短词的误报保护：这些字出现在非「最」语义时不应判违规
_FALSE_POSITIVE_CTX: dict[str, list[str]] = {
    # “最”只有在构成最高级时才违规；普通副词用法放过
    "最": ["近", "后", "终", "初", "新", "低", "多", "大", "终"],
}

# 风险等级归类
_HIGH_RISK_MED = (
    "治疗", "治愈", "根治", "断根", "诊疗", "药到病除", "消炎", "杀菌", "排毒",
    "增强免疫力", "无副作用", "零激素", "修复腹直肌", "修复盆底肌", "治疗产后漏尿",
    "治疗月子病", "催乳治疗", "消炎催乳", "医学级", "医用级",
)
_MID_RISK_ABS = (
    "最佳", "最好", "最高级", "顶级", "极致", "唯一", "国家级", "100%", "百分百",
    "绝对", "永久", "万能", "无效退款", "零风险", "安全无毒", "纯天然无添加",
    "替代母乳", "媲美母乳", "更接近母乳", "赛过母乳", "保本", "高收益", "零风险投资",
)


# ───────────────────────── 加载 ─────────────────────────

def list_platforms() -> list[dict]:
    """返回所有可用平台列表（id/名称/定位/文件）。"""
    if not _INDEX_FILE.exists():
        # 兜底：扫描目录
        return [{"platform_id": p.stem, "名称": p.stem, "文件": p.name}
                for p in sorted(PLATFORM_DIR.glob("*.json"))
                if p.name != "platform-benchmarks.json"]
    try:
        data = json.loads(_INDEX_FILE.read_text(encoding="utf-8-sig"))
        return data.get("platforms", []) or []
    except Exception:
        return []


def load_platform(platform_id: str = DEFAULT_PLATFORM) -> dict:
    """加载指定平台 JSON 档案，返回完整字典。缺失时回退抖音。"""
    pid = (platform_id or DEFAULT_PLATFORM).strip().lower()
    fp = PLATFORM_DIR / f"{pid}.json"
    if not fp.exists():
        # 回退抖音，保证不崩
        fp = PLATFORM_DIR / f"{DEFAULT_PLATFORM}.json"
    try:
        return json.loads(fp.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


# ───────────────────────── 工具：解析数值区间 ─────────────────────────

_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~～]\s*(\d+(?:\.\d+)?)\s*(min|分钟|s|秒)?")
_SINGLE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(min|分钟|s|秒)?")


def _to_sec(v: float, unit: str | None) -> float:
    """把数值按单位换算成秒（min/分钟 → ×60；其余按秒）。"""
    return v * 60.0 if unit in ("min", "分钟") else v


def _parse_range(text: str) -> tuple[float | None, float | None]:
    """从如 '2.0-2.5s' / '每5-10s' / '15-45s' / '5-15min' 文本里取 (min,max)，统一为秒。

    自动识别 min/分钟 后缀并换算；取不到返回 (None,None)。
    """
    if not text:
        return None, None
    m = _NUM_RE.search(str(text))
    if m:
        return _to_sec(float(m.group(1)), m.group(3)), _to_sec(float(m.group(2)), m.group(3))
    m = _SINGLE_RE.search(str(text))
    if m:
        v = _to_sec(float(m.group(1)), m.group(2))
        return v, v
    return None, None


def _extract_ratio(ratio_text: str) -> str:
    """从 '9:16（竖屏全屏）' 提取 '9:16'。"""
    if not ratio_text:
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*[:：]\s*(\d+(?:\.\d+)?)", str(ratio_text))
    return f"{m.group(1)}:{m.group(2)}" if m else str(ratio_text).strip()


def _extract_title_limit(text: str) -> int | None:
    """从 '55字' / '≤30字' 提取标题字数上限。"""
    if not text:
        return None
    m = re.search(r"(\d+)\s*字", str(text))
    return int(m.group(1)) if m else None


# ───────────────────────── 校准参数 ─────────────────────────

def get_calibration(platform_id: str = DEFAULT_PLATFORM) -> dict:
    """返回该平台的剪辑校准参数（已做数值解析，便于下游判断）。

    包含：画幅/时长上限/推荐甜点区/平均镜头时长区间/钩子密度/首钩位置/
          字幕风格/BGM音量/转场频率/信息密度/安全区/标题字数上限/违禁词列表。
    """
    plat = load_platform(platform_id)
    pid = plat.get("platform_id", platform_id)
    hard = plat.get("硬规则") or {}
    cal = plat.get("剪辑校准参数") or {}
    ratio_text = (hard.get("画幅") or {}).get("主推比例", "")
    shot_min, shot_max = _parse_range(cal.get("平均镜头时长目标", ""))
    sweet_min, sweet_max = _parse_range(cal.get("推荐时长甜点区", ""))
    hook_min, hook_max = _parse_range(cal.get("钩子密度", ""))
    dens_min, dens_max = _parse_range(cal.get("信息密度", ""))
    return {
        "platform_id": pid,
        "平台名称": plat.get("名称", pid),
        "画幅": _extract_ratio(ratio_text),
        "画幅支持": (hard.get("画幅") or {}).get("支持比例", []),
        "画幅安全区": cal.get("画幅安全区", (hard.get("画幅") or {}).get("安全区", "")),
        "时长": hard.get("时长", {}),
        "时长上限秒": _hard_duration_cap_sec(plat),
        "推荐甜点区": cal.get("推荐时长甜点区", ""),
        "甜点区秒": [sweet_min, sweet_max],
        "平均镜头时长目标": cal.get("平均镜头时长目标", ""),
        "平均镜头时长秒": [shot_min, shot_max],
        "钩子密度": cal.get("钩子密度", ""),
        "钩子密度间隔秒": [hook_min, hook_max],
        "首钩位置": cal.get("首钩位置", ""),
        "字幕风格": cal.get("字幕风格", {}),
        "BGM音量": cal.get("BGM音量", ""),
        "转场频率": cal.get("转场频率", ""),
        "信息密度": cal.get("信息密度", ""),
        "信息密度间隔秒": [dens_min, dens_max],
        "标题字数上限": _extract_title_limit((hard.get("标题") or {}).get("字数限制", "")),
        "违禁词列表": hard.get("违禁限流词", []) or [],
        "内容偏好": plat.get("内容偏好", {}),
    }


def _hard_duration_cap_sec(plat: dict) -> int | None:
    """根据平台硬规则给出短视频时长上限（秒）。取不到返回 None。"""
    hard = plat.get("硬规则") or {}
    d = hard.get("时长") or {}
    txt = " ".join(str(v) for v in d.values())
    m = re.search(r"(\d+)\s*min", txt)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*秒", txt)
    if m:
        return int(m.group(1))
    return None


# ───────────────────────── 违禁词扫描 ─────────────────────────

def _risk_level(word: str) -> str:
    if word in _HIGH_RISK_MED:
        return "高（医疗红线）"
    if word in _MID_RISK_ABS:
        return "中（广告法绝对化）"
    return "中低（营销限流）"


def scan_banned_words(text: str, platform_id: str = DEFAULT_PLATFORM) -> list[dict]:
    """扫描文本中的平台违禁词，返回命中列表。

    每项：{命中词, 位置(字符偏移), 风险等级, 安全替代词, 上下文片段}
    规则：
      - 长短语（>=4字）优先匹配，避免子词重复命中；
      - 单字“最”需构成最高级才判，放过“最近/最后/最终”等误报；
      - 命中后从 SAFE_REPLACEMENTS 取安全替代词。
    """
    if not text:
        return []
    cal = get_calibration(platform_id)
    banned: list[str] = list(dict.fromkeys(cal.get("违禁词列表", [])))  # 去重保序
    hits: list[dict] = []
    used_spans: list[tuple[int, int]] = []

    def _overlap(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in used_spans)

    # 按长度从长到短匹配，优先命中完整短语
    for w in sorted(banned, key=len, reverse=True):
        if not w:
            continue
        if len(w) == 1 and w == "最":
            # 最高级正则：最 + 褒义/程度字
            for m in re.finditer(r"最(好|佳|优|棒|牛|强|大|高|低|快|新|先|先进|优质|划算|值)",
                                 str(text)):
                a, b = m.span()
                if _overlap(a, b):
                    continue
                used_spans.append((a, b))
                hits.append(_hit_item(w, m.group(0), a, text))
            continue
        start = 0
        while True:
            idx = str(text).find(w, start)
            if idx < 0:
                break
            a, b = idx, idx + len(w)
            if not _overlap(a, b):
                used_spans.append((a, b))
                # 单字“最”的上下文误报保护
                if w == "最":
                    nxt = str(text)[b:b + 1]
                    if nxt in _FALSE_POSITIVE_CTX.get("最", []):
                        start = b
                        continue
                # “第一”列举误报保护：第一点/第一个问题/第一，首先… 是列举而非最高级承诺
                # 注意：转写可能为繁体（第一個問題），豁免字符集需同时覆盖简繁
                if w == "第一":
                    tail = str(text)[b:b + 5].lstrip()
                    if tail[:2] == "首先" or (tail and tail[0] in "个點步問條項次個，,：:、。"):
                        start = b
                        continue
                hits.append(_hit_item(w, str(text)[a:b], a, text))
            start = b
    hits.sort(key=lambda x: x["位置"])
    return hits


def _hit_item(rule_word: str, matched: str, offset: int, text: str) -> dict:
    ctx = str(text)[max(0, offset - 6): offset + len(matched) + 6]
    return {
        "命中词": matched,
        "规则词": rule_word,
        "位置": offset,
        "风险等级": _risk_level(rule_word),
        "安全替代词": SAFE_REPLACEMENTS.get(rule_word, SAFE_REPLACEMENTS.get(matched, "（人工改写为非医疗/非绝对化表述）")),
        "上下文": ctx,
    }


# ───────────────────────── 校准注入 edit_intent ─────────────────────────

def _collect_intent_text(intent: dict) -> str:
    """从剪辑意图字典里收集所有可能出现违禁词的文本，用于扫描。"""
    parts: list[str] = []
    cm = intent.get("内容地图") or {}
    for k in ("主题", "核心结论", "受众"):
        if cm.get(k):
            parts.append(str(cm[k]))
    if cm.get("关键实体"):
        parts.append(" ".join(str(x) for x in cm["关键实体"]))
    for it in intent.get("逐句意图") or []:
        if it.get("句"):
            parts.append(str(it["句"]))
    ad = intent.get("architecture_decision") or {}
    for k in ("钩子设计", "CTA设计", "匹配理由"):
        if ad.get(k):
            parts.append(str(ad[k]))
    for p in ad.get("段落计划") or []:
        for k in ("剪辑要点", "对应素材", "段落功能"):
            if p.get(k):
                parts.append(str(p[k]))
    for u in ad.get("剪辑语言应用点") or []:
        if u.get("手法"):
            parts.append(str(u["手法"]))
    rs = intent.get("节奏方案") or {}
    if rs.get("说明"):
        parts.append(str(rs["说明"]))
    return " ".join(parts)


def _intent_total_sec(intent: dict) -> float | None:
    """从意图字典估算成片总时长（秒）。取不到返回 None。"""
    meta = intent.get("_meta") or {}
    if isinstance(meta.get("total_sec"), (int, float)) and meta["total_sec"]:
        return float(meta["total_sec"])
    rs = intent.get("节奏方案") or {}
    ends = [float(g.get("结束秒", 0)) for g in (rs.get("bgm分段") or [])
            if isinstance(g.get("结束秒"), (int, float)) and g.get("结束秒")]
    if ends:
        return max(ends)
    return None


def calibrate_intent(edit_intent_dict: dict, platform_id: str = DEFAULT_PLATFORM) -> dict:
    """把平台校准参数注入剪辑意图字典，新增 platform_calibration 字段。

    同时做：
      - 画幅比例（写入 architecture_joint / 顶层 platform_calibration）；
      - 时长上限校验（超上限给警告+压缩建议）；
      - 节奏目标（平均镜头时长/钩子密度注入 architecture_decision.段落计划）；
      - 字幕规格 / BGM音量建议；
      - 全量文本违禁词扫描。
    返回原字典（原地修改并返回，含 platform_calibration 字段）。
    """
    intent = edit_intent_dict if isinstance(edit_intent_dict, dict) else {}
    cal = get_calibration(platform_id)
    pid = cal["platform_id"]

    # 1) 时长上限校验
    建议: list[str] = []
    total_sec = _intent_total_sec(intent)
    dur_status = "未知"
    if total_sec is not None:
        cap = cal.get("时长上限秒")
        sweet_min, sweet_max = cal.get("甜点区秒") or [None, None]
        if cap and total_sec > cap:
            dur_status = "超限"
            建议.append(f"方案时长 {total_sec:.1f}s 超过 {pid} 硬规则上限 {cap}s，"
                        f"建议压缩至 {cap}s 以内或申请长视频权限。")
        elif sweet_min and sweet_max and total_sec < sweet_min:
            dur_status = "短于甜点区"
            建议.append(f"方案时长 {total_sec:.1f}s 短于 {pid} 推荐甜点区 {cal.get('推荐甜点区','')}，"
                        f"可补充信息点提升停留时长。")
        elif sweet_min and sweet_max and total_sec > sweet_max:
            dur_status = "长于甜点区"
            建议.append(f"方案时长 {total_sec:.1f}s 长于 {pid} 泛内容甜点区上限，"
                        f"知识类可保留，否则建议压缩到 {sweet_max:.0f}s 以内提升完播率。")
        else:
            dur_status = "甜点区内"
            建议.append(f"方案时长 {total_sec:.1f}s 处于 {pid} 推荐区间，节奏安全。")

    # 2) 违禁词扫描
    scanned_text = _collect_intent_text(intent)
    hits = scan_banned_words(scanned_text, pid)
    if hits:
        建议.append(f"命中 {len(hits)} 处平台违禁/限流词，请按「安全替代词」改写后再发布"
                    f"（重点：医疗表述→护理/调养，绝对化用语→非常/领先）。")
    else:
        建议.append("未命中平台违禁词，母婴行业表述合规。")

    # 3) 节奏目标注入 architecture_decision.段落计划
    shot_min, shot_max = cal.get("平均镜头时长秒") or [None, None]
    hook_interval = cal.get("钩子密度间隔秒") or [None, None]
    ad = intent.get("architecture_decision") or {}
    plans = ad.get("段落计划") or []
    if plans and (shot_min or hook_interval):
        for p in plans:
            if shot_min:
                p.setdefault("节奏参数建议", {})
                p["节奏参数建议"].update({
                    "目标平均镜头时长秒": f"{shot_min:g}-{shot_max:g}s",
                    "钩子密度": cal.get("钩子密度", ""),
                })

    # 4) 画幅注入 architecture_joint
    joint = intent.get("architecture_joint")
    if isinstance(joint, dict):
        joint["目标平台画幅"] = cal.get("画幅")
        joint["目标平台"] = pid

    # 5) 组装 platform_calibration 字段
    block: dict[str, Any] = {
        "platform_id": pid,
        "平台名称": cal.get("平台名称"),
        "画幅": cal.get("画幅"),
        "画幅支持": cal.get("画幅支持"),
        "时长上限": cal.get("推荐甜点区") or str(cal.get("时长", "")),
        "时长上限秒": cal.get("时长上限秒"),
        "时长判定": dur_status,
        "节奏目标": {
            "平均镜头时长": cal.get("平均镜头时长目标"),
            "钩子密度": cal.get("钩子密度"),
            "首钩位置": cal.get("首钩位置"),
            "转场频率": cal.get("转场频率"),
            "信息密度": cal.get("信息密度"),
        },
        "字幕规格": {
            **(cal.get("字幕风格") or {}),
            "安全区": cal.get("画幅安全区"),
        },
        "BGM音量": cal.get("BGM音量"),
        "标题字数上限": cal.get("标题字数上限"),
        "画幅安全区": cal.get("画幅安全区"),
        "违禁词扫描": {
            "命中": hits,
            "命中数": len(hits),
            "安全": len(hits) == 0,
        },
        "校准建议": 建议,
    }
    intent["platform_calibration"] = block
    intent.setdefault("_meta", {})["platform_id"] = pid
    return intent


# ───────────────────────── 双平台对比 ─────────────────────────

def compare_platforms(platform_a: str, platform_b: str,
                      intent_dict: dict | None = None) -> dict:
    """对比两个平台的校准差异，返回差异清单 + 对方案的影响。

    返回：{平台A, 平台B, 共同项, 差异清单, 方案影响}
    """
    ca = get_calibration(platform_a)
    cb = get_calibration(platform_b)
    keys = [
        ("画幅", "画幅比例", "竖/横屏选择"),
        ("平均镜头时长目标", "节奏", "镜头切快切慢"),
        ("钩子密度", "钩子频率", "微钩子密度"),
        ("首钩位置", "钩子时机", "前3秒 vs 前30秒"),
        ("BGM音量", "BGM响度", "人声/BGM比例"),
        ("转场频率", "转场频次", "硬切 vs 自然转场"),
        ("信息密度", "信息轰炸强度", "密度 vs 逻辑连贯"),
        ("推荐甜点区", "时长", "成片长度"),
        ("字幕风格", "字幕规格", "字号/花字偏好"),
        ("标题字数上限", "标题字数", "文案长度"),
    ]
    diffs: list[dict] = []
    for field, label, impact in keys:
        va = ca.get(field)
        vb = cb.get(field)
        if json.dumps(va, ensure_ascii=False, sort_keys=True) != \
           json.dumps(vb, ensure_ascii=False, sort_keys=True):
            diffs.append({
                "参数": label,
                "平台A(" + ca.get("平台名称", platform_a) + ")": va,
                "平台B(" + cb.get("平台名称", platform_b) + ")": vb,
                "对方案的影响": impact,
            })

    # 违禁词集合差异
    set_a = set(ca.get("违禁词列表", []))
    set_b = set(cb.get("违禁词列表", []))
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)

    result: dict[str, Any] = {
        "平台A": {"platform_id": ca["platform_id"], "名称": ca.get("平台名称")},
        "平台B": {"platform_id": cb["platform_id"], "名称": cb.get("平台名称")},
        "差异清单": diffs,
        "违禁词差异": {
            "仅A有": only_a,
            "仅B有": only_b,
            "共同": sorted(set_a & set_b),
        },
        "方案影响": [],
    }
    # 基于 intent 给具体影响建议
    if intent_dict is not None:
        total_sec = _intent_total_sec(intent_dict)
        if total_sec is not None:
            sa = (ca.get("甜点区秒") or [None, None])
            sb = (cb.get("甜点区秒") or [None, None])
            if sa[0] and total_sec > sa[1] and sb[0] and total_sec < sb[0]:
                result["方案影响"].append(
                    f"当前素材 {total_sec:.0f}s：在{ca.get('平台名称')}偏长（超甜点区{sa[1]:.0f}s），"
                    f"在{cb.get('平台名称')}偏短（短于甜点区{sb[0]:.0f}s）——"
                    f"同一素材需做两版剪辑：抖音版压缩、B站版扩写。")
        shot_a = ca.get("平均镜头时长秒")
        shot_b = cb.get("平均镜头时长秒")
        if shot_a and shot_b and shot_a[0] and shot_b[0]:
            result["方案影响"].append(
                f"镜头节奏：{ca.get('平台名称')}需 {shot_a[0]:.1f}-{shot_a[1]:.1f}s/镜，"
                f"{cb.get('平台名称')}需 {shot_b[0]:.1f}-{shot_b[1]:.1f}s/镜——"
                f"同一转写素材，抖音版多切镜、B站版少切镜。")
        if ca.get("画幅") != cb.get("画幅"):
            result["方案影响"].append(
                f"画幅不同：{ca.get('平台名称')}主推 {ca.get('画幅')}，"
                f"{cb.get('平台名称')}主推 {cb.get('画幅')}——需分别竖屏/横屏两版封面与字幕安全区。")
    return result


def platform_decision_sheet(cal: dict) -> str:
    """把校准参数转成人可读决策单。"""
    if not cal:
        return "（无平台校准数据）"
    out = [
        f"目标平台：{cal.get('平台名称')}（{cal.get('platform_id')}）",
        f"画幅：{cal.get('画幅')}（安全区：{cal.get('画幅安全区', '')}）",
        f"时长：{cal.get('推荐甜点区')}（硬上限约 {cal.get('时长上限秒')}s）",
        f"节奏：平均镜头 {cal.get('平均镜头时长目标')}；{cal.get('钩子密度')}；首钩 {cal.get('首钩位置')}",
        f"字幕：{json.dumps(cal.get('字幕风格', {}), ensure_ascii=False)}",
        f"BGM音量：{cal.get('BGM音量')}",
        f"标题字数上限：{cal.get('标题字数上限')}",
        f"违禁词收录：{len(cal.get('违禁词列表', []))} 个",
    ]
    return "\n".join(out)
