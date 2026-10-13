#!/usr/bin/env python3
r"""花字 / 文本动画 自测：字段级断言 + 持久化 + 技能调用。

在 work/selftest/text 里做副本，不碰真实草稿。
覆盖：
  1. add_styled_text 字段断言（fill.color / size / bold / strokes / font / background）
  2. add_text_animation 字段断言（material_animations.animations 含 in/out 类型）
  3. 持久化：Draft.save + Draft.load 重新加载后字段仍在
  4. 技能调用：通过 skills.Registry 走 add_styled_text / add_text_animation
  5. 预设样式：style="口播标准/封面" 能读出字体与入场动画
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, edit2, skills, verify  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="花字/文本动画自测")
    ap.add_argument("--draft", default="月子知识-成片")
    args = ap.parse_args()

    src = cli.resolve_draft(args.draft)
    case = ROOT / "work" / "selftest" / "text"
    shutil.rmtree(case, ignore_errors=True)
    case.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, case)

    # ---- 1. add_styled_text 字段断言 ----
    d = Draft.load(case)
    rep = edit2.add_styled_text(
        d, "花字测试ABC", 1.0, 4.0,
        font="抖音美好体", color=[1.0, 0.83, 0.0], size=9.0, bold=True,
        border_color=[0.0, 0.0, 0.0], border_width=50,
        background_color="#333333", y=0.5,
    )
    mat = d.material(rep["material"])
    content = json.loads(mat["content"])
    s0 = content["styles"][0]
    check("花字：content 含 styles[0] 与 text", content.get("text") == "花字测试ABC" and bool(s0))
    check("花字：fill.color 写入指定 RGB",
          s0["fill"]["content"]["solid"]["color"] == [1.0, 0.83, 0.0],
          str(s0["fill"]["content"]["solid"]["color"]))
    check("花字：size=9.0 且 bold=true", s0["size"] == 9.0 and s0["bold"] is True,
          f"size={s0['size']} bold={s0['bold']}")
    check("花字：strokes 非空且宽度≈0.1",
          len(s0["strokes"]) == 1 and abs(s0["strokes"][0]["width"] - 0.1) < 1e-3,
          f"width={s0['strokes'][0]['width']}")
    check("花字：font 含 id 与 path",
          bool(s0.get("font", {}).get("id")) and s0.get("font", {}).get("path", "").endswith(".ttf"),
          s0.get("font", {}).get("path", ""))
    check("花字：背景色写入素材级字段",
          mat.get("background_color") == "#333333" and mat.get("background_alpha") == 1.0,
          str(mat.get("background_color")))
    check("花字：check_flag 含描边(8)+背景(16)=7|8|16",
          mat.get("check_flag") == 31, f"check_flag={mat.get('check_flag')}")
    seg = next(s for s in d.text_segments if s.id == rep["segment"])
    check("花字：片段 y=0.5 且时长=3.0s",
          abs(seg.raw["clip"]["transform"]["y"] - 0.5) < 1e-6 and abs(seg.length - 3.0) < 0.005,
          f"y={seg.raw['clip']['transform']['y']} len={seg.length:.3f}")

    # ---- 2. add_text_animation 字段断言 ----
    rep2 = edit2.add_text_animation(d, seg.id, intro="向上滑动", outro="渐隐",
                                    intro_duration=0.5, outro_duration=0.4)
    anim_mat = d.material(rep2["material_animation"])
    check("动画：material_animations 素材已建立", anim_mat is not None
          and anim_mat.get("type") == "sticker_animation")
    anims = anim_mat["animations"]
    check("动画：animations 数组非空且含 in/out",
          len(anims) >= 2
          and any(a["type"] == "in" for a in anims)
          and any(a["type"] == "out" for a in anims),
          str([a["type"] for a in anims]))
    in_a = next(a for a in anims if a["type"] == "in")
    check("动画：入场 start=0 duration=500000us",
          in_a["start"] == 0 and in_a["duration"] == 500000,
          f"start={in_a['start']} dur={in_a['duration']} name={in_a['name']}")
    check("动画：入场 id/resource_id 来自元数据",
          bool(in_a["id"]) and bool(in_a["resource_id"]) and in_a["name"] == "向上滑动",
          f"id={in_a['id']}")
    out_a = next(a for a in anims if a["type"] == "out")
    # seg 时长 3.0s = 3000000us，出场 0.4s 应落在结尾
    check("动画：出场 start=seg_dur-outro_dur=2600000",
          out_a["start"] == 2600000 and out_a["duration"] == 400000,
          f"start={out_a['start']} dur={out_a['duration']}")
    check("动画：片段 extra_material_refs 引用动画素材",
          rep2["material_animation"] in (seg.raw.get("extra_material_refs") or []))

    # ---- 5. 预设样式 ----
    rep3 = edit2.add_styled_text(d, "封面预设字", 5.0, 7.0, style="口播标准/封面")
    mat3 = d.material(rep3["material"])
    c3 = json.loads(mat3["content"])
    check("预设：口播标准/封面 字号=11.0", c3["styles"][0]["size"] == 11.0,
          f"size={c3['styles'][0]['size']}")
    check("预设：封面 字体=站酷酷黑体", rep3.get("字体") == "站酷酷黑体",
          f"字体={rep3.get('字体')}")

    # ---- 3. 持久化：save + reload ----
    d.save()
    d2 = Draft.load(case)
    mats = {m["id"]: m for m in d2.materials_of("texts")}
    re_mat = mats[rep["material"]]
    re_content = json.loads(re_mat["content"])
    check("持久化：重新加载后 fill.color 仍在",
          re_content["styles"][0]["fill"]["content"]["solid"]["color"] == [1.0, 0.83, 0.0])
    re_anim = None
    for am in d2.materials_of("material_animations"):
        if am["id"] == rep2["material_animation"]:
            re_anim = am
    check("持久化：重新加载后 animations 仍在且含 in/out",
          re_anim is not None and len(re_anim["animations"]) >= 2
          and {a["type"] for a in re_anim["animations"]} >= {"in", "out"})

    # ---- 4. 技能调用（走 Registry）----
    ctx = skills.Context(draft=d2)
    s_add = skills.get("add_styled_text")
    s_anim = skills.get("add_text_animation")
    check("技能：add_styled_text 已注册", s_add is not None)
    check("技能：add_text_animation 已注册", s_anim is not None)
    r_skill = s_add.run(ctx, {"text": "技能调用花字", "t0": 8.0, "t1": 9.5,
                              "font": "站酷酷黑体", "size": 7.0, "bold": True})
    check("技能：add_styled_text 调用成功且 ok=True",
          r_skill.get("ok") is True and r_skill.get("segment"))
    r_skill2 = s_anim.run(ctx, {"segment": r_skill["segment"], "intro": "渐显"})
    check("技能：add_text_animation 调用成功且含入场",
          r_skill2.get("ok") is True and any("入场" in x for x in r_skill2["动画"]),
          str(r_skill2.get("动画")))

    # ---- 综合：草稿仍可读且无重叠 ----
    check("综合：草稿可读且文本段数增加", len(d2.text_segments) >= 6,
          f"{len(d2.text_segments)} 段文本")
    check("综合：无重叠", verify.check_no_overlap(d2, "text").ok)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    if failed:
        for n, _, det in failed:
            print("  FAIL:", n, det)
    shutil.rmtree(case, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
