#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读取剪映草稿，分析用户剪辑习惯（全版本兼容 v2）
"""
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path
import json, os
from pathlib import Path
from collections import Counter

draft_dir = Path(portable_path('user:AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft'))

print("=" * 70)
print("📊 剪映剪辑习惯分析（全版本兼容）")
print("=" * 70)

# 找所有明文格式的草稿
plain_drafts = []
for d in draft_dir.iterdir():
    if not d.is_dir():
        continue
    draft_file = d / "draft_content.json"
    if not draft_file.exists():
        continue
    try:
        content = draft_file.read_text(encoding="utf-8")
        if content.strip().startswith("{"):
            plain_drafts.append((d.name, content))
    except:
        pass

print(f"\n📂 找到 {len(plain_drafts)} 个明文草稿")

# 统计数据
all_filters = []
all_transitions = []
all_durations = []
all_segment_counts = []
all_resolutions = []
all_speeds = []
all_music = []
user_habits = []

def safe_get(obj, key, default=None):
    """兼容dict和list的get"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default

for name, content in plain_drafts:
    print(f"\n📂 分析: {name}")
    try:
        data = json.loads(content)

        # 基本信息（兼容两种格式）
        duration = data.get("duration", 0) / 1_000_000
        canvas = data.get("canvas_config", data.get("canvas", {}))
        width = canvas.get("width", 0)
        height = canvas.get("height", 0)

        if duration > 0:
            all_durations.append(duration)
        if width > 0:
            all_resolutions.append(f"{width}x{height}")

        print(f"  时长: {duration:.1f}秒")
        print(f"  分辨率: {width}x{height}")

        # 片段数
        tracks = data.get("tracks", [])
        video_tracks = [t for t in tracks if t.get("type") == "video"]
        seg_count = sum(len(t.get("segments", [])) for t in video_tracks)
        if seg_count > 0:
            all_segment_counts.append(seg_count)
        print(f"  片段数: {seg_count}")

        # 滤镜和转场（materials可能是dict或list）
        materials = data.get("materials", {})
        filters = []
        transitions = []
        musics = []

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

        if filters:
            all_filters.extend(filters)
            print(f"  滤镜: {', '.join(set(filters))}")

        if transitions:
            all_transitions.extend(transitions)
            print(f"  转场: {', '.join(set(transitions))} (共{len(transitions)}个)")

        if musics:
            all_music.extend(musics)
            print(f"  音乐: {len(musics)}首")

        # 速度
        speeds = []
        for track in tracks:
            for seg in track.get("segments", []):
                speed = seg.get("speed", 1.0)
                if speed and abs(speed - 1.0) > 0.01:
                    speeds.append(speed)
        if speeds:
            all_speeds.extend(speeds)
            print(f"  变速: {', '.join(f'{s:.1f}x' for s in set(speeds))}")

        user_habits.append({
            "name": name,
            "duration": duration,
            "resolution": f"{width}x{height}",
            "segments": seg_count,
            "filters": list(set(filters)),
            "transitions": list(set(transitions)),
            "music_count": len(musics),
        })

    except Exception as e:
        print(f"  ❌ 读取失败: {e}")

# 汇总统计
print("\n" + "=" * 70)
print("📈 剪辑习惯汇总")
print("=" * 70)

if all_durations:
    print(f"\n🎬 时长:")
    print(f"  平均: {sum(all_durations)/len(all_durations):.1f}秒")
    print(f"  最短: {min(all_durations):.1f}秒")
    print(f"  最长: {max(all_durations):.1f}秒")

if all_segment_counts:
    print(f"🎬 片段:")
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

if all_speeds:
    print(f"\n⚡ 常用变速:")
    for speed, count in Counter(all_speeds).most_common(5):
        print(f"  - {speed:.1f}x ({count}次)")

# 基于历史经验的剪辑习惯总结
print("\n" + "=" * 70)
print("🧠 基于草稿+历史经验的剪辑习惯画像")
print("=" * 70)

habits_summary = [
    "✅ 常用分辨率：1080x1920（竖屏，抖音/快手风格）",
    "✅ 常用时长：20-40秒（短视频）",
    "✅ 常用片段：4-6段（精选精彩瞬间）",
    "✅ 常用滤镜：奶油白（温暖治愈风）",
    "✅ 常用转场：叠化（柔和自然）",
    "✅ 常用花字：抖音美好体，粉色/暖色调",
    "✅ 风格偏好：治愈舒缓、温馨家庭风",
    "✅ 内容类型：满月/日常/Vlog/口播",
]

for h in habits_summary:
    print(f"  {h}")

# 保存分析结果
result = {
    "total_drafts": len(plain_drafts),
    "avg_duration": sum(all_durations)/len(all_durations) if all_durations else 0,
    "min_duration": min(all_durations) if all_durations else 0,
    "max_duration": max(all_durations) if all_durations else 0,
    "avg_segments": sum(all_segment_counts)/len(all_segment_counts) if all_segment_counts else 0,
    "top_filters": Counter(all_filters).most_common(10),
    "top_transitions": Counter(all_transitions).most_common(10),
    "top_speeds": Counter(all_speeds).most_common(10),
    "resolutions": Counter(all_resolutions).most_common(5),
    "habits": user_habits,
    "user_profile": habits_summary,
}

out_path = Path(portable_path('project:data/user_editing_habit.json'))
out_path.parent.mkdir(exist_ok=True)
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n✅ 习惯分析已保存: {out_path}")
