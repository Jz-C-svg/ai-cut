#!/usr/bin/env python3
"""Ingest curated web-learning sources with provenance and hash-based dedup."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "content-library/web-learning"
SOURCES = [
    ("adobe-premiere-titles", "titling_kinetic_text", "Adobe Premiere: Create titles", "https://helpx.adobe.com/premiere/desktop/add-text-images/stylize-text/create-titles.html", "Official workflow for custom titles; font, fill, outline, color and alignment controls. Transferable lesson: hierarchy and contrast are adjustable parameters; do not copy a particular title composition verbatim.", "official_documentation", "copyrighted_reference_only"),
    ("adobe-aftereffects-text", "titling_kinetic_text", "Adobe After Effects: Creating and editing text layers", "https://helpx.adobe.com/after-effects/desktop/add-text/create-and-edit-text-layers/creating-editing-text-layers.html", "Text layers support typography, layout, title cards, lower thirds, character-level color/size/position animation and vector scaling. Useful schema cues: font, role, anchor, per-character motion and safe-size rendering.", "official_documentation", "copyrighted_reference_only"),
    ("adobe-aftereffects-animation", "titling_kinetic_text", "Adobe After Effects: Animating text", "https://helpx.adobe.com/after-effects/desktop/animating-text/text-animation/animating-text.html", "Text animator selectors allow motion at word or character granularity. Learn the parameterization, not a branded preset look.", "official_documentation", "copyrighted_reference_only"),
    ("capcut-overlay", "stickers_overlays", "CapCut: How to add overlays", "https://www.capcut.com/resource/how-to-add-capcut-overlays", "Documents separate text, sticker, video-effect/body-effect, and transition overlays. Useful for categorizing timeline items by visual role and scope.", "official_product_guide", "copyrighted_reference_only"),
    ("capcut-editing", "editing_effects_filters", "CapCut: How to use CapCut", "https://www.capcut.com/resource/how-to-use-capcut", "Lists text presets/templates, stickers, effects, transitions, and filters by use category. Learn the taxonomy and selection flow; resource availability/license is editor-specific.", "official_product_guide", "copyrighted_reference_only"),
    ("capcut-subtitles", "titling_kinetic_text", "CapCut: Add subtitles", "https://www.capcut.com/resource/how-to-add-subtitle-in-capcut", "Describes caption style families such as glow/highlight/mono/multiline and editing timing/text. Store these as style descriptors; speech correctness still needs source audio.", "official_product_guide", "copyrighted_reference_only"),
    ("capcut-stickers", "stickers_overlays", "CapCut: Custom stickers", "https://www.capcut.com/tools/custom-stickers-online", "Describes sticker scale, rotation, loops and animation controls. Learn placement/motion parameters without copying a recognizable asset.", "official_product_guide", "copyrighted_reference_only"),
    ("capcut-filters", "editing_effects_filters", "CapCut: Filters guide", "https://www.capcut.com/resource/capcut-filter", "Product reference for filters and visual elements. Use as taxonomy only; individual looks and rights depend on the actual resource.", "official_product_guide", "copyrighted_reference_only"),
    ("adobe-color-correction", "color_grading", "Adobe Premiere: Color correction effects", "https://helpx.adobe.com/premiere/desktop/correct-color/add-color-effects/about-color-correction-effects.html", "Color correction aligns shots, restores exposure, sets mood and can constrain output; use scopes to measure chroma/luminance rather than trust a preset name.", "official_documentation", "copyrighted_reference_only"),
    ("adobe-lumetri-scopes", "color_grading", "Adobe Premiere: Display Lumetri Scopes", "https://helpx.adobe.com/premiere/desktop/correct-color/add-color-effects/display-lumetri-scopes.html", "Waveform, vectorscope, histogram and parade provide measurable exposure/color evidence. Useful for robust filter recommendations and cross-shot consistency.", "official_documentation", "copyrighted_reference_only"),
    ("adobe-color-grading", "color_grading", "Adobe Premiere: Color grading overview", "https://helpx.adobe.com/premiere/desktop/correct-color/color-correction-fundamentals/about-color-grading.html", "Color wheels, curves and scopes support correction and look creation within the edit. Separate technical correction from creative style.", "official_documentation", "copyrighted_reference_only"),
    ("adobe-effects", "transitions_effects", "Adobe Premiere: Modern animations", "https://helpx.adobe.com/premiere/desktop/add-video-effects/types-of-effects/animations.html", "Documents transition/effect families and animated graphics. Use effect families only when motivated by content and keep intensity/duration configurable.", "official_documentation", "copyrighted_reference_only"),
    ("adobe-keyframes", "transitions_effects", "Adobe Premiere: Add keyframes", "https://helpx.adobe.com/premiere/desktop/add-video-effects/control-effects-and-transitions-using-keyframes/add-keyframes.html", "Keyframes animate effect properties over time; source-to-target values and their spacing define motion. Capture easing and property ranges when later reviewing examples.", "official_documentation", "copyrighted_reference_only"),
    ("adobe-transitions", "transitions_effects", "Adobe Premiere: Set and apply transitions", "https://helpx.adobe.com/premiere/desktop/add-video-effects/apply-video-transitions/set-and-apply-default-transitions.html", "Default transitions enable consistent application; transition defaults do not prove every cut needs a transition. Prioritize continuity and motivation.", "official_documentation", "copyrighted_reference_only"),
    ("blackmagic-training", "editing_color_audio_vfx", "Blackmagic Design: DaVinci Resolve training", "https://www.blackmagicdesign.com/products/davinciresolve/training/", "Official project-based training covers editing, color, Fusion compositing, Fairlight sound design/mixing, dialogue and delivery. Broad source for expanding beyond visual decoration.", "official_training", "reference_only; lesson assets have individual terms"),
    ("blackmagic-fairlight", "audio_sound_design", "Blackmagic: Fairlight Audio Guide", "https://documents.blackmagicdesign.com/UserManuals/DaVinciResolveFairlightAudioPost.pdf", "Covers dialogue, sound effects, music editing, recording, sweetening and mixing in practical projects. Use to expand learning into sound design and mix structure.", "official_training_pdf", "reference_only; do not redistribute PDF"),
    ("youtube-retention", "performance_analysis", "YouTube Help: Measure audience retention", "https://support.google.com/youtube/answer/9314415?hl=en-11", "Use retention curves to identify peaks, dips, intros and top moments; compare similar-length videos and inspect the actual content at each point. Data informs experiments, not guaranteed causality.", "official_platform_analytics_help", "facts_and_methods_only"),
    ("youtube-recommendations", "storytelling_performance", "YouTube Help: Understand content performance", "https://support.google.com/youtube/answer/16559650?hl=en", "Frames performance through appeal, engagement and satisfaction; recommends concise intros, expectation matching and experimenting with formats while measuring audience response.", "official_platform_creator_help", "facts_and_methods_only"),
    ("tiktok-creative-codes", "short_form_storytelling", "TikTok for Business: Creative Codes", "https://ads.tiktok.com/business/en-US/creative-codes", "Platform creative guidance covers vertical 9:16, UI-safe space, hook/body/close, trends, and stimulus. Commercial-ad guidance should not be treated as an organic-content guarantee.", "official_platform_ad_guidance", "facts_and_methods_only"),
    ("tiktok-creative-best-practices", "short_form_storytelling", "TikTok for Business: Creative best practices", "https://ads.tiktok.com/business/en/blog/creative-best-practices-top-performing-ads?redirected=1", "Ad guidance emphasizes native-feeling vertical creative, clear hook/body/close and early value; treat numeric ad claims as scoped evidence, not universal organic rules.", "official_platform_ad_guidance", "facts_and_methods_only"),
    ("premiere-editing", "editing_rhythm_story", "Adobe Premiere: Edit video", "https://helpx.adobe.com/uk/premiere/desktop/edit-projects/intro-to-editing/edit-video-in-premiere.html", "Basic non-destructive trim, cut, reposition, remove-gap and playback workflows. Useful as operational knowledge distinct from style imitation.", "official_documentation", "copyrighted_reference_only"),
    ("premiere-arrange", "editing_rhythm_story", "Adobe Premiere: Arrange clips into sequences", "https://helpx.adobe.com/ee/premiere-pro/how-to/arranging-clips-to-sequence.html", "Organize footage/audio/graphics, build the narrative flow before effects, select takes, maintain continuity and review trims. Generalizable process knowledge.", "official_documentation", "copyrighted_reference_only"),
    ("mixkit-license", "asset_sources_license", "Mixkit license overview", "https://mixkit.co/license/", "Mixkit uses different licenses for stock video, restricted stock video, music, SFX, templates and art; check the license attached to each actual item.", "official_license", "license_metadata_only"),
    ("mixkit-free-video-license", "asset_sources_license", "Mixkit Stock Video Free License", "https://mixkit.co/license/modal/videoFree/", "Free-license footage can be used in commercial/non-commercial projects subject to site terms; do not infer the same permissions for every item or other Mixkit categories.", "official_license", "license_metadata_only"),
    ("mixkit-sfx-license", "asset_sources_license", "Mixkit Sound Effects Free License", "https://mixkit.co/license/modal/sfxFree/", "SFX may be embedded in a larger end product, but not redistributed on its own, as stock, inside a tool/template, or with source files.", "official_license", "license_metadata_only"),
    ("pixabay-license", "asset_sources_license", "Pixabay Content License Summary", "https://pixabay.com/service/license-summary/", "Allows free use/adaptation subject to prohibitions; standalone redistribution is prohibited and third-party rights may apply. Retain item-level source/permission records.", "official_license", "license_metadata_only"),
    ("pexels-license", "asset_sources_license", "Pexels License", "https://www.pexels.com/license/", "Photos/videos are generally free to use/modify with restrictions against standalone resale, stock redistribution, implied endorsement and trademark use.", "official_license", "license_metadata_only"),
    ("commons-reuse", "asset_sources_license", "Wikimedia Commons: Reusing content", "https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia/licenses", "Every file page has its own license; verify author, source, license, attribution and share-alike terms at item level. The repository is not one blanket license.", "official_license_guidance", "license_metadata_only"),
    ("cc-by-4", "asset_sources_license", "Creative Commons Attribution 4.0", "https://creativecommons.org/licenses/by/4.0/deed.en", "CC BY allows sharing/adaptation including commercial use if attribution, license link and change notice are provided and no additional restrictions are imposed.", "official_license", "license_metadata_only"),
    ("bcut-official", "editing_effects_filters", "必剪官方网站", "https://bcut.bilibili.cn/", "Official product page describes track/framely editing, materials, subtitles, stickers, text templates, transitions and effects; use as tool-scope reference, not a rights grant for every in-app asset.", "official_product_page", "copyrighted_reference_only"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows, seen = [], {}
    for sid, topic, title, url, summary, source_type, usage in SOURCES:
        content_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        if content_hash in seen:
            continue
        seen[content_hash] = sid
        rows.append({"id": sid, "topic": topic, "title": title, "source_url": url,
                     "source_type": source_type, "summary": summary,
                     "content_sha256": content_hash, "license_status": usage,
                     "accessed_date": "2026-10-02", "deduplicated_by": "normalized_summary_sha256",
                     "learned_facts_only": True, "raw_media_downloaded": False,
                     "permission_to_repackage_source_assets": False})
    by_topic = {}
    for row in rows:
        by_topic.setdefault(row["topic"], []).append(row["id"])
    (args.out / "sources.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    for topic, ids in sorted(by_topic.items()):
        selected = [r for r in rows if r["id"] in ids]
        (args.out / f"{topic}.json").write_text(json.dumps({"topic": topic, "source_ids": ids, "items": selected}, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"version": "web-learning-v1", "sources_unique": len(rows), "topic_count": len(by_topic),
        "topics": {k: len(v) for k, v in sorted(by_topic.items())}, "dedup": "exact normalized summary SHA-256; no source media downloaded",
        "license_policy": "keep link and per-source license status; use extracted general methods only unless an asset has item-level permission",
        "accessed_date": "2026-10-02"}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = """# 联网学习库\n\n本库整理公开网页中的工具方法、视觉分类、创作分析流程和素材许可元信息。每条记录保留原始链接、访问日期、主题、摘要哈希和使用边界；按主题拆分文件，主表不重复保存摘要。\n\n记录是来源归纳，不是逐字转载，也不表示相关来源授权 AI cut 复制素材、模板、视频帧或完整课程。素材站许可需按具体条目和具体素材类别再次核验；本次没有下载原始视频、贴图、音乐、模板或付费课程，也没有使用网页图片训练模型。\n\n平台增长/广告建议具有平台和内容类型边界，不承诺流量效果。可迁移的是可验证的结构化观察方法、软件操作概念和复盘机制；每条成片仍需结合账号受众与素材做验证。\n"""
    (args.out / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
