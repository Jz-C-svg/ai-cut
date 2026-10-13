#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量读取所有剪映草稿（明文+尝试解密加密）
"""
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path
import json, os
from pathlib import Path
from collections import Counter
import subprocess

draft_dir = Path(portable_path('user:AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft'))

print("=" * 70)
print("📊 批量读取所有剪映草稿")
print("=" * 70)

# 分类草稿
plain_drafts = []
encrypted_drafts = []

for d in draft_dir.iterdir():
    if not d.is_dir():
        continue
    draft_file = d / "draft_content.json"
    if not draft_file.exists():
        continue

    # 检查是否加密
    key_file = d / "crypto_key_store.dat"
    if key_file.exists():
        encrypted_drafts.append(d.name)
    else:
        try:
            content = draft_file.read_text(encoding="utf-8")
            if content.strip().startswith("{"):
                plain_drafts.append((d.name, content))
            else:
                encrypted_drafts.append(d.name)
        except:
            encrypted_drafts.append(d.name)

print(f"\n📂 明文草稿: {len(plain_drafts)} 个")
print(f"🔒 加密草稿: {len(encrypted_drafts)} 个")

# 统计数据
all_filters = []
all_transitions = []
all_durations = []
all_segment_counts = []
all_resolutions = []
all_speeds = []
all_texts = []
all_music = []
all_stickers = []
all_kf_counts = []
analyzed_drafts = []

def safe_get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default

print("\n" + "=" * 70)
print("🔍 分析明文草稿")
print("=" * 70)

for name, content in plain_drafts:
    print(f"\n📂 {name}")
    try:
        data = json.loads(content)

        # 基本信息
        duration = data.get("duration", 0) / 1_000_000
        canvas = data.get("canvas_config", data.get("canvas", {}))
        width = canvas.get("width", 0)
        height = canvas.get("height", 0)

        if duration > 0:
            all_durations.append(duration)
        if width > 0:
            all_resolutions.append(f"{width}x{height}")

        # 片段数
        tracks = data.get("tracks", [])
        video_tracks = [t for t in tracks if t.get("type") == "video"]
        seg_count = sum(len(t.get("segments", [])) for t in video_tracks)
        if seg_count > 0:
            all_segment_counts.append(seg_count)

        # 滤镜
        materials = data.get("materials", {})
        filters = []
        transitions = []
        musics = []
        stickers = []
        texts = []

        if isinstance(materials, dict):
            for effect in materials.get("effects", []):
                if isinstance(effect, dict):
                    effect_name = effect.get("name", "")
                    effect_type = effect.get("type", "")
                    if "滤镜" in effect_name or "filter" in effect_name.lower() or effect_type == "filter":
                        filters.append(effect_name)

            for transition in materials.get("transitions", []):
                if isinstance(transition, dict):
                    trans_name = transition.get("name", "")
                    transitions.append(trans_name)

            for music in materials.get("musics", []):
                if isinstance(music, dict):
                    music_name = music.get("name", "")
                    musics.append(music_name)

            for sticker in materials.get("stickers", []):
                if isinstance(sticker, dict):
                    sticker_name = sticker.get("name", "")
                    stickers.append(sticker_name)

            for text in materials.get("texts", []):
                if isinstance(text, dict):
                    content_str = text.get("content", "")
                    if content_str:
                        import re
                        plain_text = re.sub(r'<[^>]+>', '', content_str)
                        if plain_text.strip():
                            texts.append(plain_text[:50])

        # 关键帧
        kf_count = 0
        for track in tracks:
            for seg in track.get("segments", []):
                kf_count += len(seg.get("common_keyframes", []))

        # 速度
        speeds = []
        for track in tracks:
            for seg in track.get("segments", []):
                speed = seg.get("speed", 1.0)
                if speed and abs(speed - 1.0) > 0.01:
                    speeds.append(speed)

        # 汇总
        if filters:
            all_filters.extend(filters)
        if transitions:
            all_transitions.extend(transitions)
        if texts:
            all_texts.extend(texts)
        if musics:
            all_music.extend(musics)
        if stickers:
            all_stickers.extend(stickers)
        if kf_count > 0:
            all_kf_counts.append(kf_count)
        if speeds:
            all_speeds.extend(speeds)

        analyzed_drafts.append({
            "name": name,
            "duration": duration,
            "resolution": f"{width}x{height}",
            "segments": seg_count,
            "filters": list(set(filters)),
            "transitions": list(set(transitions)),
            "texts": texts,
            "music_count": len(musics),
            "sticker_count": len(stickers),
            "keyframes": kf_count,
        })

        print(f"  ✅ {duration:.1f}秒 | {seg_count}片段 | {len(transitions)}转场 | {len(texts)}花字")

    except Exception as e:
        print(f"  ❌ 读取失败: {e}")

# 汇总统计
print("\n" + "=" * 70)
print("📈 完整剪辑习惯汇总")
print("=" * 70)

if all_durations:
    print(f"\n🎬 时长:")
    print(f"  平均: {sum(all_durations)/len(all_durations):.1f}秒")
    print(f"  最短: {min(all_durations):.1f}秒")
    print(f"  最长: {max(all_durations):.1f}秒")

if all_segment_counts:
    print(f"🎬 片段数:")
    print(f"  平均: {sum(all_segment_counts)/len(all_segment_counts):.0f}段")

if all_resolutions:
    print(f"🎬 分辨率:")
    for res, count in Counter(all_resolutions).most_common(3):
        print(f"  - {res} ({count}次)")

if all_filters:
    print(f"\n🎨 常用滤镜:")
    for name, count in Counter(all_filters).most_common(5):
        print(f"  - {name} ({count}次)")

if all_transitions:
    print(f"\n🎞️  常用转场:")
    for name, count in Counter(all_transitions).most_common(5):
        print(f"  - {name} ({count}次)")

if all_texts:
    print(f"\n✍️  花字风格（前10个）:")
    for t in all_texts[:10]:
        print(f"  - {t}")

if all_music:
    print(f"\n🎵 音乐: {len(all_music)}首")

if all_stickers:
    print(f"\n✨ 贴纸: {len(all_stickers)}个")

if all_kf_counts:
    print(f"\n🎯 关键帧:")
    print(f"  平均: {sum(all_kf_counts)/len(all_kf_counts):.0f}个")

# 加密草稿列表
print(f"\n🔒 加密草稿列表（{len(encrypted_drafts)}个）:")
for name in encrypted_drafts[:10]:
    print(f"  - {name}")
if len(encrypted_drafts) > 10:
    print(f"  ... 还有 {len(encrypted_drafts)-10} 个")

# 保存结果
result = {
    "summary": {
        "total_drafts": len(plain_drafts) + len(encrypted_drafts),
        "plain_drafts": len(plain_drafts),
        "encrypted_drafts": len(encrypted_drafts),
        "analyzed": len(analyzed_drafts),
    },
    "habits": {
        "avg_duration": sum(all_durations)/len(all_durations) if all_durations else 0,
        "min_duration": min(all_durations) if all_durations else 0,
        "max_duration": max(all_durations) if all_durations else 0,
        "avg_segments": sum(all_segment_counts)/len(all_segment_counts) if all_segment_counts else 0,
        "top_filters": Counter(all_filters).most_common(10),
        "top_transitions": Counter(all_transitions).most_common(10),
        "resolutions": Counter(all_resolutions).most_common(5),
        "text_samples": all_texts[:20],
    },
    "drafts": analyzed_drafts,
    "encrypted_list": encrypted_drafts,
}

out_path = Path(portable_path('project:data/all_drafts_analysis.json'))
out_path.parent.mkdir(exist_ok=True)
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n✅ 完整分析已保存: {out_path}")
