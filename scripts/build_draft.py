"""把剪辑决策单变成剪辑软件草稿（AI cut / 阶段：渲染层）。

用法:
    python build_draft.py 03-scripts/<名称>.sheet.json [--software <id>] [--publish]

决策单格式见 05-templates/sheet.example.json。
--software 指定目标剪辑软件（jianying/capcut/premiere/davinci/finalcut/vegas/aftereffects），
默认 jianying（保持原有剪映草稿行为不变）。
非剪映软件：决策单 → 统一 IR → editor_bridges 写出原生工程文件到 06-drafts/<软件>/。
剪映软件：沿用原有 JianYingDraft 精细渲染 + 明文草稿发布流程。
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import uuid
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

try:
    import JianYingDraft as _editor_component
except ModuleNotFoundError as exc:
    if exc.name != 'JianYingDraft':
        raise
    raise SystemExit('JianYingDraft compatibility component is not distributed in this source release. The historical modified API is not replaced by a same-name pip package. Native draft creation is pending adapter validation; use scripts/deliver_edit.py --capabilities for supported boundaries.') from None

import cleanup  # noqa: E402
import effects  # noqa: E402
import textkit  # noqa: E402
from aicut.production import project_name, validate_draft, commit_draft
from JianYingDraft import (  # noqa: E402
    SEC, AudioMaterial, VideoMaterial, ClipSettings, DraftFolder, TextStyle, TrackType,
    VideoSegment, AudioSegment, TextSegment, trange,
)


TEMPLATE_PRESETS = {"koubo-standard": "口播标准", "fangxing-showcase": "房型展示", "duibi-ceping": "口播标准"}

# Shared strict encoder: no silent property skipping or timestamp clamping.
from aicut.keyframe_execution import apply_keyframes
from aicut.effect_execution import compile_effect, compile_filter, native_picture_config


def load_config() -> dict:
    return json.loads((ROOT / "config" / "aicut.json").read_text(encoding="utf-8-sig"))


def pick(cls, kwargs: dict) -> dict:
    """只保留构造函数真正支持的参数，避免版本差异导致崩溃。"""
    allowed = set(inspect.signature(cls).parameters)
    return {k: v for k, v in kwargs.items() if k in allowed}


def resolve(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    return str(p if p.is_absolute() else (ROOT / p))


def verify_draft(draft_path: Path, expected_seconds: float) -> bool:
    """回读生成的草稿，确认片段时长单位正确（微秒）。

    踩过的坑：trange() 的数字参数按微秒解释，传秒会得到 11 微秒的长度，
    剪映里视频等于不存在。
    """
    try:
        actual = validate_draft(draft_path)['duration']
    except (ValueError, OSError) as exc:
        print(f'[校验] 失败: {exc}')
        return False
    ok = abs(actual - expected_seconds) < .1
    print(f'[校验] 总时长 {actual:.2f}s / 期望 {expected_seconds:.2f}s')
    return ok


def runtime_asset_report(sheet: dict, draft_path: Path) -> dict:
    """Compare requested decoration assets with what was actually written."""
    content_path = draft_path / 'draft_content.json'
    data = json.loads(content_path.read_text(encoding='utf-8-sig'))
    from aicut.execution_audit import audit_timeline
    timeline = audit_timeline(sheet, data, resolve)
    tracks = data.get('tracks') or []
    track_counts = {str(t.get('name')): len(t.get('segments') or []) for t in tracks}
    clips = sheet.get('clips') or []
    requested = {
        'kinetic_text': len(sheet.get('kinetic_text') or []),
        'visual_overlays': len(sheet.get('visual_plan') or []),
        'video_effects': sum(bool(c.get('特效') or c.get('effect')) for c in clips),
        'filters': sum(bool(c.get('滤镜') or c.get('filter') or (sheet.get('画面') or {}).get('滤镜')) for c in clips),
        'transitions': sum(bool(c.get('转场调用名')) for c in clips),
        'native_text_effect': bool(sheet.get('花字')),
    }
    flower = sheet.get('花字') or {}
    flower_id = str(flower.get('素材') if isinstance(flower, dict) else flower)
    flower_applied = 0
    if flower_id:
        for material in (data.get('materials') or {}).get('texts') or []:
            try:
                text_doc = json.loads(material.get('content') or '{}')
            except (TypeError, json.JSONDecodeError):
                continue
            if any(str((style.get('effectStyle') or {}).get('id')) == flower_id
                   for style in text_doc.get('styles') or []):
                flower_applied += 1
    transition_ids = {str(m.get('id')) for m in (data.get('materials') or {}).get('transitions') or []
                      if isinstance(m, dict) and m.get('id')}
    main_track = next((t for t in tracks if t.get('name') == 'main'), {})
    transition_instances = sum(
        1 for segment in (main_track.get('segments') or [])
        if transition_ids.intersection(str(ref) for ref in (segment.get('extra_material_refs') or []))
    )
    applied = {
        'kinetic_text': track_counts.get('kinetic-title', 0),
        'visual_overlays': track_counts.get('visual-overlay', 0) + track_counts.get('visual-text', 0),
        'video_effects': track_counts.get('effect', 0),
        'filters': track_counts.get('filter', 0),
        # A transition definition in materials is only a reusable resource. Count
        # instances actually referenced by main-timeline segments instead.
        'transitions': transition_instances,
        'native_text_effect': flower_applied,
    }
    checks = {}
    for key, wanted in requested.items():
        actual = applied[key]
        checks[key] = {'requested': wanted, 'applied': actual,
                       'status': 'applied' if actual >= wanted else ('not_requested' if not wanted else 'partial_or_missing')}
    from aicut.learning_execution import verify_draft_subtitles
    learning_subtitles = verify_draft_subtitles(sheet.get('learning_execution'), data)
    return {'schema_version': 'runtime-decoration-3', 'draft': str(draft_path.resolve()),
            'status': 'complete' if timeline['status'] == 'complete'
                and learning_subtitles['status'] in {'complete', 'not_requested'}
                and all(x['status'] in {'applied', 'not_requested'} for x in checks.values()) else 'needs_review',
            'learning_subtitles': learning_subtitles,
            'timeline': timeline,
            'checks': checks, 'track_segments': track_counts,
            'material_counts': {k: len(v) for k, v in (data.get('materials') or {}).items() if isinstance(v, list)}}



def media_duration(path: str, ffprobe: str) -> float:
    """用 ffprobe 取素材真实时长（秒）。"""
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", path],
                          capture_output=True, text=True, encoding="utf-8", errors="ignore",
                          timeout=30, check=True)
    try:
        value = float(proc.stdout.strip())
        return value if math.isfinite(value) and value > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def source_range_issues(path: str, end: float, cache: dict[str, float]) -> list[str]:
    """Check a video-backed source interval against a probed source duration.

    Still images have no meaningful source timeline; their duration is defined
    by the edit timeline and is therefore exempt from the source-end check.
    """
    suffix = Path(path).suffix.lower()
    if suffix in {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}:
        return []
    try:
        duration = cache.get(path)
        if duration is None:
            duration = media_duration(path, load_config()['ffprobe'])
            cache[path] = duration
        if not math.isfinite(duration) or duration <= 0:
            return [f'无法探测视频素材时长: {path}']
        if end > duration + .05:
            return [f'片段终点 {end:.3f}s 超出源素材时长 {duration:.3f}s: {path}']
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        return [f'探测视频素材时长失败: {path} ({type(exc).__name__})']
    return []
def quality_gates(sheet: dict) -> list[str]:
    """按口播剪辑质量门做基础检查；返回问题列表。"""
    problems: list[str] = []
    if (sheet.get('source_review') or {}).get('blocking'):
        problems.append('剪辑方案与原始素材存在未修复冲突')
    try:
        project_name(sheet.get('project'))
    except ValueError as exc:
        problems.append(str(exc))
    clips = sheet.get("clips") or []
    if not isinstance(clips, list) or any(not isinstance(c, dict) for c in clips):
        return problems + ['clips 必须为片段对象列表']
    if not clips:
        problems.append("没有片段（clips 为空）")
    from aicut.content_guard import coverage_issues
    required=sheet.get('required_source_ranges') or []
    if not isinstance(required,list):
        problems.append('required_source_ranges 必须为列表')
    else:
        problems.extend(coverage_issues(clips,required,resolve))
    total = 0.0
    source_durations: dict[str, float] = {}
    for i, clip in enumerate(clips, 1):
        try:
            start, end, speed = float(clip['start']), float(clip['end']), float(clip.get('speed', 1))
            if not all(math.isfinite(v) for v in (start, end, speed)) or start < 0 or end <= start or speed <= 0:
                raise ValueError()
            total += (end - start) / speed
        except (ValueError, TypeError, KeyError):
            problems.append(f'第 {i} 个片段时间或速度非法')
            continue
        source = resolve(clip.get('file', ''))
        if source and Path(source).is_file():
            problems.extend(source_range_issues(source, end, source_durations))
    if total <= 0:
        problems.append("总时长为 0")
    try:
        limit=float(sheet.get('duration_limit',180))
        if not math.isfinite(limit) or not 1<=limit<=1800:raise ValueError()
        if total > limit:
            problems.append(f"总时长 {total:.0f}s 超过本次时长上限 {limit:.0f}s")
    except (ValueError,TypeError):
        problems.append('duration_limit 必须为 1..1800 秒')
    if not sheet.get("cover_text"):
        problems.append("缺少 cover_text：前 2 秒没有可见的价值承诺")
    if not sheet.get("cta"):
        problems.append("缺少 cta：落点不明确（只保留一个行动指令）")
    for i, c in enumerate(clips, 1):
        if not Path(resolve(c.get("file", "")) or "").is_file():
            problems.append(f"第 {i} 个片段素材不存在: {c.get('file')}")
    for subtitle_key in ("subtitles", "subtitles_json", "subtitle_file", "subtitle_ass"):
        subtitle = resolve(sheet.get(subtitle_key))
        if subtitle and not Path(subtitle).is_file():
            problems.append(f"字幕文件不存在: {sheet.get(subtitle_key)}")
    for i,item in enumerate(sheet.get('broll') or [],1):
        try:
            s,e,at,d=float(item['start']),float(item['end']),float(item['at']),float(item['duration'])
            if not all(math.isfinite(v) for v in (s,e,at,d)) or not 0<=s<e or d<=0 or at<0 or at+d>total+.05:
                raise ValueError('区间无效')
            source = resolve(item.get('file'))
            if not Path(source or '').is_file():raise ValueError('素材不存在')
            problems.extend(source_range_issues(source, e, source_durations))
        except (ValueError,TypeError,KeyError) as exc:problems.append(f'B-roll {i}: {exc}')
    cards=sheet.get('kinetic_text') or []
    for i,card in enumerate(cards,1):
        try:
            s,e=float(card['start']),float(card['end'])
            if not math.isfinite(s+e) or not 0<=s<e<=total+.05 or not card.get('title'):raise ValueError()
            y=float(card.get('layout_y',145))
            if not math.isfinite(y) or not 100<=y<=1200:raise ValueError()
        except (ValueError,TypeError,KeyError):problems.append(f'花字 {i} 时间或标题无效')
    for key in ('bgm', 'voiceover'):
        audio = sheet.get(key)
        if audio:
            if not Path(resolve(audio.get('file')) or '').is_file():
                problems.append(f'{key} 音频文件不存在')
            try:
                vol = float(audio.get('volume', 1))
                if not math.isfinite(vol) or not 0 <= vol <= 4:
                    raise ValueError()
            except (TypeError, ValueError):
                problems.append(f'{key} 音量无效')
    return problems


def _build_in_dir(sheet: dict, cfg: dict, draft_dir: Path) -> Path:
    name = sheet["project"]
    width = int(sheet.get("width") or cfg["render"]["width"])
    height = int(sheet.get("height") or cfg["render"]["height"])
    fps = int(sheet.get("fps") or cfg["render"]["fps"])

    draft_dir.mkdir(parents=True, exist_ok=True)
    folder = DraftFolder(str(draft_dir))
    script = folder.create_draft(name, width, height, fps, allow_replace=True)
    preset_name = sheet.get("style") or TEMPLATE_PRESETS.get(sheet.get("template"), "口播标准")
    presets = textkit.load_presets()
    preset = presets.get(preset_name) or presets["口播标准"]
    sheet = dict(sheet)
    sheet['画面'] = native_picture_config(sheet, preset)


    made_tracks: set[str] = set()

    def ensure_track(track_type, track_name: str) -> None:
        if track_name not in made_tracks:
            script.add_track(track_type, track_name)
            made_tracks.add(track_name)

    # 粗剪清理：删掉拍摄口令（如 3 2 1 开始），片段与字幕按剪后时间线重排
    cleanup_cfg = cleanup.load_rules()
    src_end = max(float(c["end"]) for c in sheet["clips"])
    transcript_segments = []
    _tpath = resolve(sheet.get("subtitles_json"))
    if _tpath and Path(_tpath).exists():
        transcript_segments = json.loads(Path(_tpath).read_text(encoding="utf-8-sig")).get("segments", [])
    cuts = [] if sheet.get('timeline_prepared') else cleanup.find_cut_ranges(transcript_segments, cleanup_cfg, src_end)
    if cuts:
        keeps = cleanup.keep_ranges(cuts, src_end, cleanup_cfg)
        removed = ", ".join(f"{s:.2f}-{e:.2f}s" for s, e in cuts)
        print(f"[粗剪] 删除口令 {len(cuts)} 段: {removed}")
        expanded = []
        for _clip in sheet["clips"]:
            cs, ce = float(_clip["start"]), float(_clip["end"])
            for ks, ke in keeps:
                a, b = max(cs, ks), min(ce, ke)
                if b - a > .001:
                    nc = dict(_clip)
                    nc["start"], nc["end"] = a, b
                    expanded.append(nc)
        sheet = dict(sheet)
        sheet["clips"] = expanded
        transcript_segments = cleanup.drop_hidden(transcript_segments, cuts)
        for _seg in transcript_segments:
            _seg["start"] = cleanup.map_time(float(_seg["start"]), cuts)
            _seg["end"] = cleanup.map_time(float(_seg["end"]), cuts)

    # 主视频轨道：按决策单顺序依次排列，片段从 0s 起
    script.add_track(TrackType.video, "main")
    cursor = 0.0
    for clip in sheet["clips"]:
        clip_start = cursor
        start = max(0.0, float(clip["start"]))
        end = float(clip["end"])
        # 用草稿库自己的素材时长做基准（它和剪映的判定一致，ffprobe 会有毫秒级差异）
        material = VideoMaterial(str(Path(resolve(clip["file"]))))
        mat_sec = material.duration / 1_000_000
        if mat_sec and end > mat_sec:
            print(f"[收敛] 片段结束时间 {end:.3f}s 超出素材时长 {mat_sec:.3f}s，已按素材长度裁剪")
            end = mat_sec
        if start >= mat_sec and mat_sec:
            print(f"[收敛] 起点 {start:.3f}s 超出素材范围，已归零")
            start = 0.0
        dur = end - start
        if dur < 0.05:
            raise RuntimeError(f"片段 {clip['file']} 有效时长不足 0.05s")
        speed = float(clip.get("speed") or 1.0)
        target_dur = dur / speed if speed > 0 else dur
        seg = VideoSegment(
            material,
            trange(cursor * SEC, target_dur * SEC),
            **pick(VideoSegment, {
                "source_timerange": trange(start * SEC, dur * SEC),
                "speed": speed if abs(speed - 1.0) > 1e-6 else None,
                "volume": clip.get("volume", 1.0),
            }),
        )
        if abs(speed - 1.0) > 1e-6:
            print(f"[变速] {speed:.2f}x  {dur:.2f}s -> {target_dur:.2f}s")
        # 按风格加转场（作用于该片段与下一段之间）
        tr_name = clip.get("转场调用名")
        if tr_name:
            tr_sec = float(clip.get("转场时长秒", 0.5))
            _tr = effects.resolve_transition(tr_name)
            if _tr is None:
                print(f"[转场] 跳过（剪映枚举无此转场）: {tr_name}", file=sys.stderr)
            else:
                seg.add_transition(_tr, duration=f"{tr_sec}s")
                print(f"[转场] {tr_name} {tr_sec}s")

        script.add_segment(seg, "main")

        # 关键帧动画（决策单 clip["keyframes"]，无字段则返回 0，零行为变化）
        n_kf = apply_keyframes(seg, clip, int(round(target_dur * SEC)))
        if n_kf:
            print(f"[关键帧] {Path(clip['file']).name} 写入 {n_kf} 个关键帧")

        # 片段级特效与滤镜（按片段添加）
        effect = compile_effect(clip)
        if effect:
            ensure_track(TrackType.effect, "effect")
            script.add_effect(effect.member,
                              trange(clip_start * SEC, target_dur * SEC), "effect", params=effect.params)
            print(f"[特效] {Path(clip['file']).name} 加 {effect.name}")
        filt = compile_filter(clip, sheet['画面'])
        if filt:
            ensure_track(TrackType.filter, "filter")
            script.add_filter(filt.member,
                              trange(clip_start * SEC, target_dur * SEC), "filter", intensity=filt.intensity)
            print(f"[滤镜] {Path(clip['file']).name} 加 {filt.name} {filt.intensity:.0f}")

        cursor += target_dur
    total = cursor

    # Loop background music to the edited timeline, preserving the configured gain.
    bgm = sheet.get('bgm') or {}
    if bgm.get('file'):
        music = AudioMaterial(resolve(bgm['file']))
        if music.duration <= 0:
            raise ValueError('背景音乐时长无效')
        ensure_track(TrackType.audio, 'bgm')
        at_us, total_us = 0, int(round(total * SEC))
        while at_us < total_us:
            length = min(music.duration, total_us - at_us)
            segment = AudioSegment(music, trange(at_us, length),
                                   source_timerange=trange(0, length),
                                   volume=float(bgm.get('volume', .15)))
            script.add_segment(segment, 'bgm')
            at_us += length
        if bgm.get('segments'):
            print('[BGM] 已铺满时间线；情绪分段暂保留为编辑建议')

    # 样式预设：决定字体、字号、描边、位置、动画
    sub_cfg = dict(preset.get("字幕", {}))
    layout = sheet.get('subtitle_layout') or {}
    if layout:
        sub_cfg.update({'字号': float(layout.get('draft_font_size', sub_cfg.get('字号', 6))),
                        '位置': float(layout.get('draft_position_y', sub_cfg.get('位置', -.78)))})

    # B-roll：在人声之上叠一层画面（静音），用于「讲到什么就切到什么」
    broll = sheet.get("broll") or []
    if broll:
        ensure_track(TrackType.video, "broll")
        added = 0
        for item in broll:
            path = resolve(item.get("file"))
            if not path or not Path(path).exists():
                print(f"[B-roll] 跳过不存在的素材: {item.get('file')}")
                continue
            start = float(item.get("start", 0))
            end = float(item.get("end", start + 2))
            dur = float(item.get("duration") or (end - start))
            at = float(item.get("at", 0))
            if dur <= 0.1:
                continue
            src_len = max(0.1, end - start)
            speed = None
            if src_len + 0.05 < dur:
                sp = max(0.5, src_len / dur)
                if sp < 0.5:
                    dur = src_len
                else:
                    speed = sp
                    print(f"[B-roll] 素材偏短，用 {sp:.2f}x 慢放铺满 {dur:.1f}s")
            mat = VideoMaterial(str(Path(path)))
            seg = VideoSegment(mat, trange(at * SEC, dur * SEC),
                               **pick(VideoSegment, {
                                   "source_timerange": trange(start * SEC, src_len * SEC),
                                   "speed": speed,
                                   "volume": 0.0,
                               }))
            script.add_segment(seg, "broll")
            added += 1
        print(f"[B-roll] 叠加 {added} 段画面")

    # 旁白配音（Edge-TTS）：铺一条 audio 轨道，并把词级 SRT 导入为独立 text 轨道
    vo = sheet.get("voiceover")
    if vo:
        vo_file = resolve(vo.get("file"))
        if vo_file and Path(vo_file).exists():
            vo_vol = float(vo.get("volume", 1.0))
            audio_mat = AudioMaterial(str(Path(vo_file)))
            vo_dur = audio_mat.duration / 1_000_000  # 素材时长微秒 -> 秒
            if vo_dur > total + .1:
                raise ValueError(f'配音 {vo_dur:.2f}s 长于画面 {total:.2f}s，请延长画面或缩短配音')
            script.add_track(TrackType.audio, "voiceover")
            vo_seg = AudioSegment(
                audio_mat,
                trange(0, vo_dur * SEC),
                **pick(AudioSegment, {"volume": vo_vol}),
            )
            script.add_segment(vo_seg, "voiceover")
            print(f"[配音] 音频轨道 voiceover：{Path(vo_file).name} {vo_dur:.1f}s 音量 {vo_vol}")
            # 词级字幕：优先 import_srt（库自带，自动建轨道 voiceover-sub）
            vo_srt = resolve(vo.get("srt"))
            if vo_srt and Path(vo_srt).exists():
                try:
                    script.import_srt(vo_srt, "voiceover-sub")
                    print(f"[配音] 词级字幕导入 voiceover-sub：{Path(vo_srt).name}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[配音] import_srt 失败({type(exc).__name__}: {exc})，跳过词级字幕")
            else:
                print(f"[配音] 未找到 SRT，跳过词级字幕: {vo.get('srt')}")
        else:
            print(f"[配音] 未找到音频文件，跳过: {vo.get('file')}")

    # 字幕：优先用逐字稿 JSON（可做字体加工），否则退回直接导入 SRT
    transcript = resolve(sheet.get("subtitles_json"))
    srt = resolve(sheet.get("subtitles"))
    if transcript and Path(transcript).exists():
        script.add_track(TrackType.text, "subtitle")
        added = 0
        if layout.get('single_line_per_caption'):
            from aicut.text_overlays import split_caption_timeline
            transcript_segments = split_caption_timeline(transcript_segments,
                max_chars=int(layout.get('max_chars_per_line', 12)))
        for seg in transcript_segments:
            start = float(seg["start"])
            end = min(float(seg["end"]), total)
            if start >= total or end - start < 0.05:
                continue
            sub_seg = TextSegment(seg["text"], trange(start * SEC, (end - start) * SEC),
                                  font=textkit.font(sub_cfg.get("字体")),
                                  style=textkit.style_of(sub_cfg),
                                  border=textkit.border_of(sub_cfg),
                                  clip_settings=textkit.clip_of(sub_cfg))
            textkit.add_animation_to(sub_seg, sub_cfg)
            script.add_segment(sub_seg, "subtitle")
            added += 1
        print(f"[字幕] 逐字稿生成 {added} 句，字体 {sub_cfg.get('字体')} 字号 {sub_cfg.get('字号')}")
    elif srt:
        script.add_track(TrackType.text, "subtitle")
        script.import_srt(srt, "subtitle")
        print(f"[字幕] 导入 {Path(srt).name}（未做字体加工，建议补上 subtitles_json）")

    # 封面承诺：套用预设字体、描边与入场动画
    cover = sheet.get("cover_text")
    if cover and not sheet.get('kinetic_text'):
        cov_cfg = preset.get("封面", {})
        script.add_track(TrackType.text, "cover")
        cover_len = min(float(cov_cfg.get("时长", 2.5)), total)
        cover_seg = TextSegment(cover, trange(0, cover_len * SEC),
                                font=textkit.font(cov_cfg.get("字体")),
                                style=textkit.style_of(cov_cfg),
                                border=textkit.border_of(cov_cfg),
                                clip_settings=textkit.clip_of(cov_cfg))
        textkit.add_animation_to(cover_seg, cov_cfg)
        script.add_segment(cover_seg, "cover")
        print(f"[封面] 字体 {cov_cfg.get('字体')} 字号 {cov_cfg.get('字号')} 动画 {cov_cfg.get('入场动画')}")

    # Source-anchored cards are editable native text, not only burned-in ASS.
    if sheet.get('kinetic_text'):
        ensure_track(TrackType.text,'kinetic-title')
        ensure_track(TrackType.text,'kinetic-subtitle')
        for card in sheet['kinetic_text']:
            start,end=float(card['start']),float(card['end'])
            cfg_title={'字号':8.0,'加粗':True,'颜色':[.82,.92,.72],'位置':.76,
                       '描边':{'颜色':[.09,.13,.16],'宽度':42},'入场动画':'向上滑动','入场时长':.22}
            text=' '.join(str(x) for x in (card.get('label'),card['title']) if x)
            seg=TextSegment(text,trange(start*SEC,(end-start)*SEC),style=textkit.style_of(cfg_title),
                            border=textkit.border_of(cfg_title),clip_settings=textkit.clip_of(cfg_title))
            textkit.add_animation_to(seg,cfg_title);script.add_segment(seg,'kinetic-title')
            if card.get('subtitle'):
                cfg_sub={'字号':4.3,'位置':.65,'描边':{'颜色':[.09,.13,.16],'宽度':40}}
                sub=TextSegment(card['subtitle'],trange(start*SEC,(end-start)*SEC),style=textkit.style_of(cfg_sub),
                                border=textkit.border_of(cfg_sub),clip_settings=textkit.clip_of(cfg_sub))
                script.add_segment(sub,'kinetic-subtitle')
        print(f'[花字] 已生成 {len(sheet["kinetic_text"])} 组可编辑标题与说明')

    script.save()

    # -- v3.7.0 视觉智能适配引擎：把决策单里的视觉计划写进草稿 --
    _vplan = sheet.get('visual_plan') or []
    if _vplan:
        try:
            sys.path.insert(0, str(ROOT / 'lib'))
            from aicut import visualoverlay  # noqa: E402
            _vstats = visualoverlay.apply_to_draft(draft_dir / name, _vplan)
            print(f"[视觉叠加] added={_vstats['added']} skipped={_vstats['skipped']} by_type={_vstats['by_type']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[视觉叠加] 失败({type(exc).__name__}: {exc})，不影响主草稿")

    # 关键词局部高亮：数字与指定关键词换色加粗
    hl_cfg = preset.get("高亮", {})
    if hl_cfg:
        n = textkit.apply_highlights(draft_dir / name, hl_cfg, sheet.get("keywords"))
        print(f"[高亮] 处理 {n} 句字幕（规则 {hl_cfg.get('规则')}）")
    # 花字：把素材库里真实的剪映花字挂到指定文字上
    fx = sheet.get("花字")
    if fx:
        rid = fx.get("素材") if isinstance(fx, dict) else fx
        where = fx.get("位置", "cover") if isinstance(fx, dict) else "cover"
        cnt = effects.apply_text_effect(draft_dir / name, str(rid), where)
        print(f"[花字] 已挂载 {cnt} 处文字（素材 {rid}，位置 {where}）")
        if isinstance(fx, dict) and fx.get('自动选择') and cnt < 1:
            raise RuntimeError(f"自动选择的剪映花字资源 {rid} 未挂载到 {where} 轨道")
    draft_path = draft_dir / name
    (draft_path / 'aicut.execution-sheet.json').write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding='utf-8')
    report = runtime_asset_report(sheet, draft_path)
    report_path = draft_path / 'aicut-runtime-assets.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[实时素材调用] {report['status']}；报告 {report_path}")
    for key, item in report['checks'].items():
        if item['status'] == 'partial_or_missing':
            print(f"[实时素材调用未完成] {key}: requested={item['requested']} applied={item['applied']}", file=sys.stderr)
    print(f"[草稿] {draft_path}  总时长 {total:.1f}s  {width}x{height}@{fps}")
    return draft_path


def build(sheet: dict, cfg: dict, *, output_root: Path | None = None) -> Path:
    from aicut.editor_capabilities import preflight
    preflight(sheet, 'jianying', 'project')
    project_name(sheet['project'])
    lengths = {}
    for clip in sheet['clips']:
        source = resolve(clip['file'])
        if source not in lengths:
            lengths[source] = media_duration(source, cfg['ffprobe'])
        if lengths[source] <= 0 or float(clip['start']) >= lengths[source] or float(clip['end']) > lengths[source] + .1:
            raise ValueError(f'片段超出素材范围: {source}')
    for item in sheet.get('broll') or []:
        source=resolve(item['file'])
        if source not in lengths:lengths[source]=media_duration(source,cfg['ffprobe'])
        if float(item['end'])>lengths[source]+.1:raise ValueError(f'B-roll 超出素材范围: {source}')
    staging = ROOT / 'work' / 'build-staging' / uuid.uuid4().hex
    source = _build_in_dir(sheet, cfg, staging)
    validate_draft(source)
    from editor_bridges.jianying.integrity import check_content
    integrity_errors = check_content(json.loads((source / 'draft_content.json').read_text(encoding='utf-8-sig')))
    if integrity_errors:
        raise ValueError('Draft integrity: ' + '; '.join(integrity_errors))
    staged_report = json.loads((source / 'aicut-runtime-assets.json').read_text(encoding='utf-8'))
    if staged_report['status'] != 'complete':
        raise ValueError(f'草稿执行校验未通过，保留诊断目录: {source}')
    target = (output_root or ROOT / '06-drafts') / sheet['project']
    from aicut import snapshot
    result = commit_draft(source, target, lambda p: snapshot.snapshot(p, note='重新生成前自动备份'))
    print(f'[提交] {target}；上一版备份: {result["backup"] or "无"}')
    effective_sheet = json.loads((target / 'aicut.execution-sheet.json').read_text(encoding='utf-8'))
    report = runtime_asset_report(effective_sheet, target)
    report_path = target / 'aicut-runtime-assets.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[实时素材调用] {report['status']}；报告 {report_path}")
    for key, item in report['checks'].items():
        if item['status'] == 'partial_or_missing':
            print(f"[实时素材调用未完成] {key}: requested={item['requested']} applied={item['applied']}", file=sys.stderr)
    return target


def sniff_draft_plaintext(draft_path: Path) -> bool:
    """嗅探草稿是否为明文 JSON：draft_content.json 必须非空且以 '{' 开头。

    剪映 6.0+ 的草稿可能被 AES-256 加密保存（加密串不以 { 开头，如
    "0fd8WDB7..."）。本项目只读写明文草稿，加密草稿一律拒绝发布/读写。
    """
    f = draft_path / "draft_content.json"
    if not f.exists():
        return False
    try:
        head = f.read_bytes()[:64]
    except OSError:
        return False
    return bool(head) and head.lstrip()[:1] == b"{"


def publish(draft_path: Path, cfg: dict) -> None:
    target_root = Path(cfg["draft_root"])
    if not target_root.exists():
        raise FileNotFoundError(f'剪映草稿目录不存在: {target_root}')
    if not sniff_draft_plaintext(draft_path):
        raise ValueError('源草稿不是有效明文草稿')
    target = target_root / draft_path.name
    from aicut import snapshot
    result = commit_draft(draft_path, target, lambda p: snapshot.snapshot(p, note='发布替换前自动备份'))
    print(f'[发布备份] {result["backup"] or "首次发布"}')
    print(f"[发布] 已复制到剪映草稿目录: {target}\n        重启剪映后可在草稿列表中看到")



def build_for_software(sheet: dict, software_id: str) -> Path:
    """非剪映软件路径：决策单 → 统一 IR → 原生工程文件。

    输出位置：06-drafts/<软件>/<工程名>/<工程名><扩展名>。
    """
    sys.path.insert(0, str(ROOT / "lib"))
    from editor_bridges import registry  # noqa: PLC0415
    from aicut import sheet2ir  # noqa: PLC0415

    bridge = registry.get_bridge(software_id)
    from aicut.editor_capabilities import preflight
    preflight(sheet, software_id, 'project')
    if software_id in ('jianying', 'capcut'):
        written = build(sheet, load_config(), output_root=ROOT / '06-drafts' / software_id)
        manifest = {'software': software_id, 'project': str(written / 'draft_content.json'),
                    'writer': 'shared_native_sheet_writer', 'executed_in_editor': False,
                    'host_import_verified': False, 'host_render_verified': False}
        (written / 'export.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return written / 'draft_content.json'
    project = sheet2ir.sheet_to_project(sheet, ROOT)
    if sheet.get('self_contained_assets') and software_id in ('jianying', 'capcut'):
        project.extra.update(self_contained_assets=True, media_root=str(ROOT))
    name = project.name
    out_dir = ROOT / "06-drafts" / software_id / name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}{bridge.project_ext}"
    written = bridge.write(project, out_path)
    n_seg = len(project.all_segments)
    print(f"[待宿主验证] {software_id} 草稿交换产物 {written}")
    print(f"[草稿] 轨道 {len(project.tracks)} 个 / 片段 {n_seg} 个 / 总时长 {project.duration:.1f}s"
          f"  {project.width}x{project.height}@{project.fps:.0f}")
    # 附一份统一 IR 快照，便于回读与跨软件迁移
    (out_dir / "project.ir.json").write_text(project.to_json(), encoding="utf-8")
    validation = bridge.validate(written)
    if not validation.valid:
        raise RuntimeError(f"{software_id} 工程写出后校验失败: {'; '.join(validation.errors)}")
    readback: dict = {}
    try:
        parsed = bridge.read(written)
        readback = {
            "name": parsed.name,
            "duration": parsed.duration,
            "tracks": len(parsed.tracks),
            "segments": len(parsed.all_segments),
        }
    except Exception as exc:
        # 某些桥接器的主产物是供宿主软件执行的脚本，不能假装已经回读为原生工程。
        readback = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    manifest = {
        "schema_version": "aicut-editor-export-1.0",
        "software": software_id,
        "software_name": bridge.software_name,
        "project": str(written),
        "ir_snapshot": str(out_dir / "project.ir.json"),
        "validation": validation.to_dict(),
        "readback": readback,
        "compatibility": {
            "status": "unverified_in_target_editor",
            "basis": "internal_writer_reader_roundtrip_only",
            "note": "内部格式自检不等于目标软件可导入；需在对应软件打开并检查时间线。",
        },
        "execution": {
            "generated": True,
            "executed_in_editor": False,
            "note": "需要在目标软件中打开工程或运行桥接脚本后，才算真正执行",
        },
    }
    (out_dir / "export.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[内部校验] {software_id} 写后校验通过，宿主导入待验证；清单 {out_dir / 'export.manifest.json'}")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="AI cut 草稿生成（多剪辑软件）")
    ap.add_argument("sheet", help="决策单 JSON 路径")
    ap.add_argument("--software", default="jianying",
                    help="目标剪辑软件 id：jianying/capcut/premiere/davinci/finalcut/vegas/aftereffects（默认 jianying）")
    ap.add_argument("--publish", action="store_true", help="复制到剪映草稿目录（仅 jianying 生效）")
    ap.add_argument("--force", action="store_true", help="忽略质量门问题继续生成")
    ap.add_argument('--approve-plan-sha256', help='确认决策单的 SHA256；修改方案后旧确认失效')
    args = ap.parse_args()

    cfg = load_config()
    sheet = json.loads(Path(args.sheet).read_text(encoding="utf-8-sig"))
    if sheet.get('requires_plan_confirmation'):
        import hashlib
        digest = hashlib.sha256(Path(args.sheet).read_bytes()).hexdigest()
        if args.approve_plan_sha256 != digest:
            print(f'[待确认方案] 尚未确认当前版本。SHA256={digest}')
            return 4

    problems = quality_gates(sheet)
    hard_problems = [p for p in problems if not p.startswith(('缺少 cover_text', '缺少 cta', '总时长 '))]
    if hard_problems:
        print('[质量门] ' + '; '.join(hard_problems))
        return 2
    if problems:
        print("质量门检查：")
        for p in problems:
            print(f"  ! {p}")
        if not args.force:
            print("\n已停止。修正后重跑，或用 --force 忽略。")
            return 2

    software_id = args.software.strip().lower()
    if software_id == "jianying":
        try:
            draft_path = build(sheet, cfg)
        except Exception as exc:
            print(f'[失败] {exc}')
            return 3
        runtime_report = json.loads((draft_path / 'aicut-runtime-assets.json').read_text(encoding='utf-8'))
        if runtime_report.get('status') != 'complete':
            print('[失败] 花字/特效素材调用未全部落到草稿；草稿已保留，详见 aicut-runtime-assets.json')
            return 4
        if args.publish:
            try:
                publish(draft_path, cfg)
            except Exception as exc:
                print(f'[发布失败] {exc}')
                return 3
    else:
        try:
            build_for_software(sheet, software_id)
        except ValueError as exc:
            print(f'[失败] {exc}')
            return 3
        except Exception as exc:  # noqa: BLE001
            print(f'[失败] {software_id} 工程写出异常: {type(exc).__name__}: {exc}')
            return 3
        if args.publish:
            print(f"[跳过发布] --publish 仅适用于剪映；{software_id} 工程文件已直接生成，"
                  f"用对应软件打开即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
