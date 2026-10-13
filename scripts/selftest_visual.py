#!/usr/bin/env python3
r"""视觉智能适配引擎 v3.7.0 自测：规则库 + 叠加引擎 + 素材库。

用法:
    cd <project-root>
    venv\Scripts\python.exe scripts\selftest_visual.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from aicut import visualmap  # noqa: E402
from aicut import visualoverlay  # noqa: E402

PASS, FAIL = 0, 0
CHECKS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        CHECKS.append(f"  [OK] {name}")
    else:
        FAIL += 1
        CHECKS.append(f"  [FAIL] {name}  {detail}")


def has_type(plan, t):
    return any(it["type"] == t for it in plan)


def content_contains(plan, t, sub):
    return any(it["type"] == t and sub in str(it["content"]) for it in plan)


def main() -> int:
    # ---------- 1. 数字规则（含中文数字转换） ----------
    seg_num = [{"start": 0.0, "end": 3.0, "text": "宝宝两到三天就会出现"}]
    p = visualmap.plan(seg_num, None, "medium")
    check("数字规则命中", has_type(p, "number_card"))
    check("中文数字转换 两到三天->2-3天", content_contains(p, "number_card", "2-3天"),
          str([it["content"] for it in p]))

    seg_week = [{"start": 0.0, "end": 3.0, "text": "一般两周之内会消退"}]
    p2 = visualmap.plan(seg_week, None, "medium")
    check("中文数字 两周->14天", content_contains(p2, "number_card", "14天"),
          str([it["content"] for it in p2]))

    seg_arab = [{"start": 0.0, "end": 3.0, "text": "每次喂奶 30 毫升"}]
    p3 = visualmap.plan(seg_arab, None, "medium")
    check("阿拉伯数字命中", content_contains(p3, "number_card", "30毫升"),
          str([it["content"] for it in p3]))

    # ---------- 2. 症状规则 ----------
    # 注：症状词典按"首个命中"匹配，重症句需不含更靠前的普通症状词，
    # 否则先命中普通词返回医疗标（这是引擎确定性行为，详见测试报告 29.9.1）。
    seg_sym = [{"start": 0.0, "end": 3.0, "text": "宝宝确诊肺炎要小心"}]
    p4 = visualmap.plan(seg_sym, None, "medium")
    check("症状规则命中", has_type(p4, "warning_label"))
    severe = [it for it in p4 if it["type"] == "warning_label"]
    check("重症症状用警示三角", severe and severe[0]["asset"] == "icon_warning.png",
          str(severe[0]["asset"] if severe else None))

    # ---------- 3. 动作规则 ----------
    seg_act = [{"start": 0.0, "end": 3.0, "text": "喂奶以后记得拍嗝"}]
    p5 = visualmap.plan(seg_act, None, "medium")
    check("动作规则命中", has_type(p5, "action_icon"))
    burp = [it for it in p5 if it["type"] == "action_icon"]
    check("拍嗝->icon_burp", burp and burp[0]["asset"] == "icon_burp.png")

    # ---------- 4. 结论规则 ----------
    seg_con = [{"start": 0.0, "end": 3.0, "text": "这种情况需要及时就医"}]
    p6 = visualmap.plan(seg_con, None, "medium")
    check("结论/警示句命中", has_type(p6, "conclusion_card"))

    # ---------- 5. 钩子句识别（语义档案高光候选） ----------
    segs = [
        {"start": 0.0, "end": 2.0, "text": "开头铺垫"},
        {"start": 2.0, "end": 6.0, "text": "关键知识点展开讲"},
    ]
    doc = {"高光候选": [{"t": 2.0, "理由": "关键起点"}]}
    p7 = visualmap.plan(segs, doc, "medium")
    check("钩子句识别（高光覆盖第二句）", has_type(p7, "hook_title"))
    hook_items = [it for it in p7 if it["type"] == "hook_title"]
    check("钩子不落到首句空隙", hook_items and hook_items[0]["t0"] == 2.0,
          str([it["t0"] for it in hook_items]))

    # ---------- 6. density 差异 ----------
    dense = [
        {"start": 0.0, "end": 2.0, "text": "正常的护理步骤"},
        {"start": 2.0, "end": 5.0, "text": "五到七天是高峰"},
    ]
    plow = visualmap.plan(dense, None, "low")
    pmed = visualmap.plan(dense, None, "medium")
    phigh = visualmap.plan(dense, None, "high")
    check("density low 比 medium 少", len(plow) <= len(pmed),
          f"low={len(plow)} med={len(pmed)}")
    check("density high 含 keyword_highlight", has_type(phigh, "keyword_highlight"))
    check("stats 挂载", getattr(plow, "_stats", {}).get("总句数") == 2)

    # ---------- 7. 位置计算 ----------
    c_tr = visualoverlay.position_to_clip("top_right")
    check("位置 top_right 位移", abs(c_tr["transform_x"] - 0.5) < 1e-6 and c_tr["scale_x"] < 0.5)
    c_t = visualoverlay.position_to_clip("top_center", as_text=True)
    check("文字段缩放恒为1", c_t["scale_x"] == 1.0 and c_t["transform_x"] == 0.0)
    c_br = visualoverlay.position_to_clip("bottom_right")
    check("bottom_right 居下", abs(c_br["transform_y"] - 0.35) < 1e-6)

    # ---------- 8. 素材库存在 ----------
    lib = ROOT / "content-library" / "visuals"
    pngs = list(lib.glob("*.png"))
    check("素材库 PNG >=20", len(pngs) >= 20, f"got {len(pngs)}")
    mf = lib / "manifest.json"
    check("manifest.json 存在", mf.exists())
    need = ["icon_clock.png", "icon_warning.png", "icon_feeding.png",
            "icon_burp.png", "card_warning.png", "icon_heart.png"]
    missing = [n for n in need if not (lib / n).exists()]
    check("关键素材齐全", not missing, str(missing))

    # ---------- 9. 叠加写入草稿（造一个最小草稿 JSON） ----------
    fake_dir = ROOT / "work" / "_selftest_visual_draft"
    fake_dir.mkdir(parents=True, exist_ok=True)
    draft_json = {
        "materials": {"videos": [], "texts": [], "speeds": []},
        "tracks": [{"attribute": 0, "flag": 0, "id": "t0", "is_default_name": False,
                    "name": "main", "segments": [], "type": "video"}],
    }
    (fake_dir / "draft_content.json").write_text(json.dumps(draft_json), encoding="utf-8")
    plan = [
        {"t0": 1.0, "t1": 3.0, "type": "number_card", "content": "2-3天",
         "asset": "icon_clock.png", "position": "top_right",
         "style": {"font_size": 9.5, "color": "#FF9F43", "bg_color": "#FECA57", "bold": True},
         "reason": "数字", "source_text": "两到三天", "confidence": 0.9},
        {"t0": 3.0, "t1": 5.0, "type": "hook_title", "content": "重点提醒",
         "asset": None, "position": "top_center",
         "style": {"font_size": 14.0, "color": "#FECA57", "bg_color": "#FF9F43", "bold": True},
         "reason": "钩子", "source_text": "钩子句", "confidence": 0.95},
    ]
    stats = visualoverlay.apply_to_draft(fake_dir, plan, lib)
    check("叠加 added=2", stats["added"] == 2, str(stats))
    out = json.loads((fake_dir / "draft_content.json").read_text(encoding="utf-8"))
    names = [t["name"] for t in out["tracks"]]
    check("生成 visual-overlay 轨道", "visual-overlay" in names)
    check("生成 visual-text 轨道", "visual-text" in names)
    check("图片素材写入 materials.videos", len(out["materials"]["videos"]) == 1)
    check("文本素材写入 materials.texts", len(out["materials"]["texts"]) == 1)
    check("speed 素材已挂", len(out["materials"]["speeds"]) >= 1)
    # 时间对齐
    vseg = [t for t in out["tracks"] if t["name"] == "visual-overlay"][0]["segments"][0]
    check("叠加时间=1.0s", vseg["target_timerange"]["start"] == 1_000_000,
          str(vseg["target_timerange"]))
    check("时长=2.0s", vseg["target_timerange"]["duration"] == 2_000_000)

    # ---------- 10. 空计划处理 ----------
    stats0 = visualoverlay.apply_to_draft(fake_dir, [])
    check("空计划 added=0", stats0["added"] == 0)

    # ---------- 汇总 ----------
    print("\n".join(CHECKS))
    total = PASS + FAIL
    print(f"\n视觉适配引擎自测: {PASS}/{total} 通过, {FAIL} 失败")
    if FAIL:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
