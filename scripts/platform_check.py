#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""平台评测清单（platform_check.py）——剪辑方案生成后自动核对平台标准。

对一份 edit_intent（剪辑意图）字典按目标平台做 8 项合规评测，
每项给出 PASS / WARN / FAIL + 详情 + 改进建议，最后汇总 0-100 分与 S/A/B/C 等级。

评测项：
  1 时长合规   2 画幅比例   3 节奏达标   4 字幕合规
  5 BGM音量    6 违禁词扫描 7 标题字数   8 信息密度

用法（CLI）：
  python scripts/platform_check.py --intent path/to/intent.json --platform douyin
  python scripts/platform_check.py --intent intent.json --platform bilibili --out report.json

用法（作为库）：
  from platform_check import evaluate_intent
  rep = evaluate_intent(intent_dict, "douyin")
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import platform_calibrator as pc  # noqa: E402

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# 各项权重（合计 100）：违禁词合规最重（母婴蓝V红线），时长/画幅/节奏次之
WEIGHTS = {
    "时长合规": 15,
    "画幅比例": 15,
    "节奏达标": 15,
    "字幕合规": 10,
    "BGM音量": 5,
    "违禁词扫描": 20,
    "标题字数": 10,
    "信息密度": 10,
}


def _total_sec(intent: dict) -> float | None:
    meta = intent.get("_meta") or {}
    if isinstance(meta.get("total_sec"), (int, float)) and meta["total_sec"]:
        return float(meta["total_sec"])
    rs = intent.get("节奏方案") or {}
    ends = [float(g.get("结束秒", 0)) for g in (rs.get("bgm分段") or [])
            if isinstance(g.get("结束秒"), (int, float)) and g.get("结束秒")]
    return max(ends) if ends else None


def _all_text(intent: dict) -> str:
    """汇总方案中所有会被发布/显示的文本（标题/正文/字幕/钩子/CTA）。"""
    parts: list[str] = []
    cm = intent.get("内容地图") or {}
    for k in ("主题", "核心结论", "受众"):
        if cm.get(k):
            parts.append(str(cm[k]))
    for it in intent.get("逐句意图") or []:
        if it.get("句"):
            parts.append(str(it["句"]))
    ad = intent.get("architecture_decision") or {}
    for k in ("钩子设计", "CTA设计"):
        if ad.get(k):
            parts.append(str(ad[k]))
    for p in ad.get("段落计划") or []:
        if p.get("剪辑要点"):
            parts.append(str(p["剪辑要点"]))
    if intent.get("标题"):
        parts.append(str(intent["标题"]))
    return " ".join(parts)


def _shot_estimate(intent: dict, total_sec: float) -> float:
    """估算平均镜头时长（秒/镜）：用逐句意图或段落计划数近似镜头数。"""
    n = max(len(intent.get("逐句意图") or []),
            len((intent.get("architecture_decision") or {}).get("段落计划") or []),
            1)
    return round(total_sec / n, 2)


def evaluate_intent(intent: dict, platform_id: str = "douyin") -> dict:
    """对一份剪辑意图做平台合规评测，返回结构化报告。"""
    cal = pc.get_calibration(platform_id)
    pid = cal["platform_id"]
    total = _total_sec(intent)
    checks: list[dict] = []

    def add(name: str, status: str, detail: str, suggest: str = ""):
        checks.append({"评测项": name, "结果": status, "详情": detail,
                       "改进建议": suggest, "权重": WEIGHTS[name]})

    # 1) 时长合规
    cap = cal.get("时长上限秒")
    s_lo, s_hi = cal.get("甜点区秒") or [None, None]
    if total is None:
        add("时长合规", WARN, "方案未提供总时长（_meta.total_sec / 节奏方案.结束秒 缺失）",
            "补全时长后再评测，或运行完整 generate_intent。")
    elif cap and total > cap:
        add("时长合规", FAIL, f"时长 {total:.1f}s 超过 {pid} 硬上限 {cap}s",
            f"压缩至 {cap}s 以内，或申请 {pid} 长视频权限。")
    elif s_lo and s_hi and s_lo <= total <= s_hi:
        add("时长合规", PASS, f"时长 {total:.1f}s 落在推荐甜点区 {cal.get('推荐甜点区')}")
    elif s_lo and s_hi and total < s_lo:
        add("时长合规", WARN, f"时长 {total:.1f}s 短于甜点区下限 {s_lo:.0f}s",
            "可补充信息点/案例，提升停留时长与完播率。")
    else:
        add("时长合规", WARN, f"时长 {total:.1f}s 长于甜点区上限 {s_hi:.0f}s",
            "知识类可保留；泛娱乐建议压缩到甜点区以保完播。")

    # 2) 画幅比例
    joint = intent.get("architecture_joint") or {}
    chosen = joint.get("目标平台画幅") or (intent.get("platform_calibration") or {}).get("画幅") \
        or cal.get("画幅")
    expect = cal.get("画幅")
    if chosen == expect:
        add("画幅比例", PASS, f"目标画幅 {chosen} 符合 {pid} 主推 {expect}")
    elif chosen in (cal.get("画幅支持") or []):
        add("画幅比例", WARN, f"目标画幅 {chosen} 为 {pid} 支持但非主推比例",
            f"建议改用主推 {expect} 以获得最佳竖/横屏沉浸感与流量。")
    else:
        add("画幅比例", FAIL, f"目标画幅 {chosen} 与 {pid} 主推 {expect} 不一致",
            f"按 {expect} 重新构图/裁切，并重排字幕安全区。")

    # 3) 节奏达标（平均镜头时长）
    if total is None or total <= 0:
        add("节奏达标", WARN, "缺总时长，无法核算平均镜头时长", "补全时长后评测。")
    else:
        avg_shot = _shot_estimate(intent, total)
        t_lo, t_hi = cal.get("平均镜头时长秒") or [None, None]
        if t_lo is None:
            add("节奏达标", WARN, f"方案估算平均镜头 {avg_shot}s，但平台未给镜头时长区间",
                "")
        elif t_lo <= avg_shot <= t_hi:
            add("节奏达标", PASS,
                f"估算平均镜头 {avg_shot}s，命中 {pid} 目标 {t_lo:g}-{t_hi:g}s")
        elif avg_shot < t_lo:
            add("节奏达标", WARN,
                f"估算平均镜头 {avg_shot}s 快于 {pid} 目标 {t_lo:g}-{t_hi:g}s",
                "镜头过碎，可适当延长画面停留，减少无效硬切。")
        else:
            add("节奏达标", WARN,
                f"估算平均镜头 {avg_shot}s 慢于 {pid} 目标 {t_lo:g}-{t_hi:g}s",
                "节奏偏慢，建议加快镜头切换、加密微钩子以提升完播。")

    # 4) 字幕合规
    sub = cal.get("字幕风格") or {}
    if sub.get("字号") and sub.get("位置"):
        add("字幕合规", PASS,
            f"字幕规格符合 {pid}：{sub.get('字号')}｜{sub.get('位置')}",
            f"安全区：{cal.get('画幅安全区', '')}")
    else:
        add("字幕合规", WARN, "平台字幕规格不完整", "按平台字幕风格补全字号/位置/安全区。")

    # 5) BGM 音量
    bgm = cal.get("BGM音量")
    if bgm:
        add("BGM音量", PASS, f"{pid} 标准：{bgm}", "")
    else:
        add("BGM音量", WARN, "平台未给 BGM 音量标准", "按经验 BGM 低于人声 12-15dB。")

    # 6) 违禁词扫描
    hits = pc.scan_banned_words(_all_text(intent), pid)
    n_high = sum(1 for h in hits if "高" in h["风险等级"])
    if not hits:
        add("违禁词扫描", PASS, "未命中平台违禁/限流词", "")
    elif n_high:
        add("违禁词扫描", FAIL,
            f"命中 {len(hits)} 处（含 {n_high} 处医疗红线词）："
            + "、".join(h["命中词"] for h in hits[:5]),
            "立即按安全替代词改写（治疗→护理、根治→调养、修复腹直肌→腹直肌护理等）。")
    else:
        add("违禁词扫描", WARN,
            f"命中 {len(hits)} 处广告法/营销限流词："
            + "、".join(h["命中词"] for h in hits[:5]),
            "按安全替代词改写（最→非常、第一→领先、100%→大幅）。")

    # 7) 标题字数
    title = str((intent.get("内容地图") or {}).get("主题") or intent.get("标题") or "")
    tlimit = cal.get("标题字数上限")
    if not title:
        add("标题字数", WARN, "方案未提供标题/主题", f"拟定标题，控制在 {tlimit} 字内。")
    elif tlimit and len(title) > tlimit:
        add("标题字数", FAIL, f"标题 {len(title)} 字超过 {pid} 上限 {tlimit} 字",
            f"压缩到 {tlimit} 字内，前 10-15 字放核心搜索词。")
    else:
        add("标题字数", PASS, f"标题 {len(title)} 字 ≤ 上限 {tlimit} 字", "")

    # 8) 信息密度
    if total is None or total <= 0:
        add("信息密度", WARN, "缺总时长，无法核算信息密度", "补全时长后评测。")
    else:
        ad = intent.get("architecture_decision") or {}
        n_info = len(ad.get("段落计划") or []) or len(intent.get("逐句意图") or [])
        gap = total / max(1, n_info)
        d_lo, d_hi = cal.get("信息密度间隔秒") or [None, None]
        if d_lo is None:
            add("信息密度", WARN, f"方案约 {n_info} 个信息点，平台未给密度区间", "")
        elif d_lo <= gap <= (d_hi or d_lo * 1.5):
            add("信息密度", PASS,
                f"约每 {gap:.1f}s 一个信息点，命中 {pid} 建议 {cal.get('信息密度')}")
        else:
            add("信息密度", WARN,
                f"约每 {gap:.1f}s 一个信息点，偏离 {pid} 建议 {cal.get('信息密度')}",
                "过密则喘息，过疏则加料——按平台信息密度节奏调整。")

    # 汇总评分
    score = 0.0
    for c in checks:
        if c["结果"] == PASS:
            score += c["权重"]
        elif c["结果"] == WARN:
            score += c["权重"] * 0.5
        else:
            score += 0.0
    score = round(score, 1)
    grade = "S" if score >= 90 else "A" if score >= 78 else "B" if score >= 65 else "C"
    return {
        "platform_id": pid,
        "平台名称": cal.get("平台名称"),
        "评测时间": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "评测项": checks,
        "汇总评分": score,
        "平台适配度等级": grade,
        "PASS数": sum(1 for c in checks if c["结果"] == PASS),
        "WARN数": sum(1 for c in checks if c["结果"] == WARN),
        "FAIL数": sum(1 for c in checks if c["结果"] == FAIL),
    }


def report_text(rep: dict) -> str:
    out = [f"【平台评测报告】{rep['平台名称']}（{rep['platform_id']}）"
           f"—— 评分 {rep['汇总评分']}/100  等级 {rep['平台适配度等级']}"
           f"（PASS {rep['PASS数']} / WARN {rep['WARN数']} / FAIL {rep['FAIL数']}）"]
    for c in rep["评测项"]:
        mark = {"PASS": "✔", "WARN": "▲", "FAIL": "✘"}[c["结果"]]
        out.append(f"  {mark} [{c['结果']}] {c['评测项']}：{c['详情']}")
        if c["改进建议"]:
            out.append(f"      → 建议：{c['改进建议']}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="平台评测清单：核对剪辑方案是否符合目标平台标准")
    ap.add_argument("--intent", required=True, help="剪辑意图 JSON 路径")
    ap.add_argument("--platform", default="douyin", help="目标平台 id（默认 douyin）")
    ap.add_argument("--out", default="", help="可选：评测报告 JSON 输出路径")
    args = ap.parse_args()

    intent = json.loads(Path(args.intent).read_text(encoding="utf-8"))
    rep = evaluate_intent(intent, args.platform)
    print(report_text(rep))
    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已保存：{outp}")


if __name__ == "__main__":
    main()
