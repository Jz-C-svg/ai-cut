#!/usr/bin/env python3
r"""TOP3 草稿引擎四件套自测：文本阴影 + 关键词高亮 + 画中画混合模式 + 视频淡入淡出。

字段级断言（不是"看起来对"）：
  阴影      文本素材外层 has_shadow=True 且 shadow_color/shadow_alpha 正确；
            content JSON styles[0].shadows[0] 含 color/diffuse/content
  高亮      content.styles 长度 = 普通段 + 关键词段 + 普通段（关键词命中时=3）；
            styles[].range 连续非重叠，长度之和 = utf16_len(text)；
            高亮段 fill.solid.color == highlight_color；is_rich_text=True
  混合模式  materials.effects 含 type=mix_mode 且 effect_id/resource_id 正确；
            片段 extra_material_refs 引用了该素材
  淡入淡出  materials.material_animations 含 type=sticker_animation；
            animations[] 含 type=in(渐显) 与 type=out(渐隐)，start/duration 正确
  持久化    save + reload 后上述字段仍在
  可加载    Draft.load() 不抛异常，时长为正

全部在 work/selftest/engine4 里做副本，不碰真实草稿。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, edit2  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="TOP3 引擎四件套自测")
    ap.add_argument("--draft", default="月子知识-成片")
    args = ap.parse_args()

    src = cli.resolve_draft(args.draft)
    case = ROOT / "work" / "selftest" / "engine4"
    shutil.rmtree(case, ignore_errors=True)
    case.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, case)

    d = Draft.load(case)
    print(f"样本：{src} · {d.declared_duration:.3f}s · 视频 {len(d.video_segments)} · 文本 {len(d.text_segments)}")

    # ---- 1. 关键词高亮 ----
    text = "产后坐月子一定要注意保暖"
    keywords = ["保暖"]
    rep_hl = edit2.add_highlighted_subtitle(
        d, text, 0.5, 3.0,
        keywords=keywords,
        highlight_color=(1.0, 0.85, 0.2),
        base_color=(1.0, 1.0, 1.0),
        size=6.0, bold=True,
    )
    hl_mat = d.material(rep_hl["material"])
    content = json.loads(hl_mat["content"])

    check("高亮：is_rich_text=True", content.get("is_rich_text") is True,
          f"is_rich_text={content.get('is_rich_text')}")
    # 关键词在末尾时 styles=2（前+关键词），在中间时 styles=3；都合法
    check("高亮：styles 长度 >= 2 且 = 关键词段+相邻普通段",
          2 <= len(content["styles"]) <= 3,
          f"styles={len(content['styles'])} 段")

    # range 连续非重叠
    ranges = [tuple(s["range"]) for s in content["styles"]]
    contiguous = True
    cursor = 0
    for s, e in ranges:
        if s != cursor or s >= e:
            contiguous = False
            break
        cursor = e
    check("高亮：range 连续非重叠且覆盖整段",
          contiguous and cursor == len(text),
          f"ranges={ranges} · total={len(text)}")

    # 高亮段颜色正确（中间那段）
    hl_color = content["styles"][1]["fill"]["content"]["solid"]["color"]
    check("高亮：关键词段颜色 = (1.0, 0.85, 0.2)",
          all(abs(a - b) < 1e-3 for a, b in zip(hl_color, [1.0, 0.85, 0.2])),
          f"color={hl_color}")

    # ---- 2. 文本阴影 ----
    rep_sh = edit2.add_shadow(
        d, rep_hl["segment"],
        color=(0.0, 0.0, 0.0), alpha=0.9,
        offset_x=0.3, offset_y=0.3, blur=0.45,
    )
    sh_mat = d.material(rep_sh["material"])
    check("阴影：外层 has_shadow=True", sh_mat.get("has_shadow") is True,
          f"has_shadow={sh_mat.get('has_shadow')}")
    check("阴影：外层 shadow_color=#000000 且 alpha=0.9",
          sh_mat.get("shadow_color") == "#000000" and abs(sh_mat.get("shadow_alpha", 0) - 0.9) < 1e-6,
          f"color={sh_mat.get('shadow_color')} alpha={sh_mat.get('shadow_alpha')}")
    sh_content = json.loads(sh_mat["content"])
    sh0 = sh_content["styles"][0].get("shadows") or []
    check("阴影：content.styles[0].shadows[0] 含 color 与 diffuse",
          len(sh0) == 1 and "content" in sh0[0] and "diffuse" in sh0[0],
          f"shadows[0].keys={list(sh0[0].keys()) if sh0 else []}")

    # ---- 3. 画中画混合模式 ----
    vid0 = d.video_segments[0]
    rep_mm = edit2.set_mix_mode(d, vid0.id, "滤色")
    mm_mat = d.material(rep_mm["material"])
    check("混合模式：materials.effects 含 type=mix_mode",
          mm_mat.get("type") == "mix_mode" and mm_mat.get("name") == "滤色",
          f"type={mm_mat.get('type')} name={mm_mat.get('name')}")
    check("混合模式：片段 extra_material_refs 引用了该素材",
          rep_mm["material"] in (vid0.raw.get("extra_material_refs") or []),
          f"refs={vid0.raw.get('extra_material_refs')}")

    # ---- 4. 视频淡入淡出 ----
    vid1 = d.video_segments[1] if len(d.video_segments) > 1 else d.video_segments[0]
    rep_fade = edit2.add_video_fade(d, vid1.id, in_seconds=0.5, out_seconds=0.5)
    fade_mat = d.material(rep_fade["material_animation"])
    anims = fade_mat.get("animations") or []
    types = {a.get("type") for a in anims}
    check("淡入淡出：material_animations 含 sticker_animation",
          fade_mat.get("type") == "sticker_animation",
          f"type={fade_mat.get('type')}")
    check("淡入淡出：animations 含 in(渐显) 与 out(渐隐)",
          "in" in types and "out" in types,
          f"types={types} · {rep_fade['动画']}")
    in_item = next(a for a in anims if a.get("type") == "in")
    out_item = next(a for a in anims if a.get("type") == "out")
    check("淡入淡出：in.start=0 且 out.duration=0.5s",
          in_item.get("start") == 0 and out_item.get("duration") == 500000,
          f"in={in_item.get('start')}us/{in_item.get('duration')}us · out start={out_item.get('start')}us dur={out_item.get('duration')}us")

    # ---- 5. 逐字级 words[] ----
    pc_text = "月子中心"
    pc_colors = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 1.0, 0.0)]
    rep_pc = edit2.add_per_char_text(
        d, pc_text, 0.5, 2.5,
        colors=pc_colors, size=6.0, bold=True,
    )
    pc_mat = d.material(rep_pc["material"])
    pc_content = json.loads(pc_mat["content"])
    words = pc_content.get("words") or []
    check("逐字：words[] 非空且长度 == 字数",
          len(words) == len(pc_text),
          f"words={len(words)} / text={len(pc_text)}")
    check("逐字：每个元素含 char_index/color/size 或 char_index/color",
          all("char_index" in w and "color" in w for w in words),
          f"keys={list(words[0].keys()) if words else []}")
    check("逐字：首字颜色 = (1,0,0) 且 is_rich_text=True",
          all(abs(a - b) < 1e-3 for a, b in zip(words[0]["color"], [1.0, 0.0, 0.0]))
          and pc_content.get("is_rich_text") is True,
          f"color={words[0]['color']}")

    # ---- 6. 持久化：save + reload 后字段仍在 ----
    d.save()
    rl = Draft.load(case)
    rl_hl_mat = rl.material(rep_hl["material"])
    rl_hl_content = json.loads(rl_hl_mat["content"])
    check("持久化：高亮 styles 长度与 is_rich_text 仍在",
          len(rl_hl_content["styles"]) >= 2 and rl_hl_content.get("is_rich_text") is True,
          f"styles={len(rl_hl_content['styles'])}")
    rl_pc_mat = rl.material(rep_pc["material"])
    rl_pc_words = (json.loads(rl_pc_mat["content"]).get("words") or [])
    check("持久化：逐字 words[] 长度仍为 4",
          len(rl_pc_words) == 4,
          f"words={len(rl_pc_words)}")
    rl_sh_mat = rl.material(rep_sh["material"])
    check("持久化：阴影 has_shadow 仍为 True",
          rl_sh_mat.get("has_shadow") is True,
          f"has_shadow={rl_sh_mat.get('has_shadow')}")
    rl_mm_mat = rl.material(rep_mm["material"])
    check("持久化：混合模式 type=mix_mode 仍在",
          rl_mm_mat.get("type") == "mix_mode",
          f"type={rl_mm_mat.get('type')}")
    rl_fade_mat = rl.material(rep_fade["material_animation"])
    rl_types = {a.get("type") for a in (rl_fade_mat.get("animations") or [])}
    check("持久化：淡入淡出 in/out 动画仍在",
          "in" in rl_types and "out" in rl_types,
          f"types={rl_types}")

    # ---- 7. 草稿可加载且时长为正 ----
    check("加载：Draft.load() 正常且时长为正",
          rl.declared_duration > 0 and len(list(rl.all_segments())) > 0,
          f"{rl.declared_duration:.3f}s · {len(list(rl.all_segments()))} 片段")

    shutil.rmtree(case, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
