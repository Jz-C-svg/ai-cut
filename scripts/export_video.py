#!/usr/bin/env python3
r"""成片导出：按决策单用 ffmpeg 直接渲染 MP4（不依赖剪映导出）。

保留：片段区间、变速、清晰明亮滤镜、字幕、动态花字、BGM 与画幅标准化。
剪映光晕特效以轻量 FFmpeg 柔光近似；其他剪映专有特效/转场仍需在剪映导出。

用法:
    python export_video.py --sheet 03-scripts\x.sheet.json [--out 07-output\x.mp4] [--no-subs]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import uuid
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))
from aicut.production import read_json, project_name
W, H, FPS = 1080, 1920, 30


def load_sheet(path: Path) -> dict:
    return read_json(path)


def resolve(raw: str) -> str:
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    if not p.is_file():
        raise FileNotFoundError(f"素材不存在: {p}")
    return str(p)


def probe_duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=60, check=True).stdout
    return float(out.strip() or 0)


def has_audio(path):
    proc = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a',
                           '-show_entries', 'stream=index', '-of', 'json', path],
                          capture_output=True, text=True, timeout=60, check=True)
    return bool(json.loads(proc.stdout).get('streams'))


def tempo_filters(speed):
    chain = []
    while speed > 2:
        chain.append('atempo=2'); speed /= 2
    while speed < .5:
        chain.append('atempo=0.5'); speed *= 2
    return ','.join([*chain, f'atempo={speed:.8f}'])


def srt_time(sec: float) -> str:
    ms = int(round(float(sec) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ensure_srt(sheet: dict, name: str) -> str | None:
    """字幕统一转成 SRT：JSON 逐字稿也可以直接用。"""
    raw = sheet.get("subtitles_json")
    if not raw:
        return None
    src = Path(raw)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        return None
    if src.suffix.lower() == ".srt":
        return str(src)
    data = json.loads(src.read_text(encoding="utf-8"))
    lines = []
    for i, seg in enumerate(data.get("segments", []), 1):
        lines.append(f"{i}\n{srt_time(seg['start'])} --> {srt_time(seg['end'])}\n{seg['text']}\n")
    out = ROOT / "work" / f"{name}.export.srt"
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)


def ensure_ass(sheet: dict, name: str) -> str | None:
    raw = sheet.get("subtitles_json")
    if not raw:
        return None
    src = Path(raw)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists() or src.suffix.lower() == ".ass":
        return str(src) if src.exists() else None
    data = json.loads(src.read_text(encoding="utf-8"))
    layout = sheet.get('subtitle_layout') or {}
    font_size = float(layout.get('font_size_px', 42))
    single_line = bool(layout.get('single_line_per_caption'))
    default_margin_v = max(0, round(1920 - int(layout.get('top_px', 1040)) - font_size)) if single_line else 220
    margin_v = int(layout.get('margin_bottom_px', default_margin_v))
    font_name = str(layout.get('font_name') or ("Microsoft YaHei" if single_line else "Noto Sans SC"))
    bold = 1 if single_line else 0
    background = bool(layout.get('background', single_line))
    border_style = 3 if single_line and background else 1
    outline = float(layout.get('outline_width_px', 3 if single_line else 3))
    shadow = float(layout.get('shadow_px', 0))
    outline_colour = str(layout.get('outline_colour_ass', '&H00000000'))
    back_colour = str(layout.get('back_colour_ass', '&H80000000' if background else '&HFF000000'))
    if (not math.isfinite(font_size) or not 24 <= font_size <= 120
            or not 0 <= margin_v <= 1500 or not math.isfinite(outline) or not 0 <= outline <= 12
            or not math.isfinite(shadow) or not 0 <= shadow <= 10):
        raise ValueError('subtitle layout outside supported range')
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{font_size:g},&H00FFFFFF,&H00FFFFFF,{outline_colour},"
        f"{back_colour},{bold},0,0,0,100,100,0,0,{border_style},{outline:g},{shadow:g},2,60,60,{margin_v},1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text",
    ]
    segments = data.get("segments", []) if isinstance(data, dict) else data
    if single_line:
        from aicut.text_overlays import split_caption_timeline
        segments = split_caption_timeline(segments,
            max_chars=int(layout.get('max_chars_per_line', 12)))
    for seg in segments:
        def ass_time(sec: float) -> str:
            total = max(0, int(round(float(sec) * 100)))
            cs = total % 100
            total //= 100
            s = total % 60
            total //= 60
            m = total % 60
            h = total // 60
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
        text = str(seg["text"]).replace("\n", r"\N").replace("{", r"\{").replace("}", r"\}")
        lines.append(f"Dialogue: 0,{ass_time(seg['start'])},{ass_time(seg['end'])},Default,,0,0,0,,{text}")
    out = ROOT / "work" / f"{name}.export.ass"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return str(out)


def subtitle_path(sheet: dict, out_path: Path) -> str | None:
    """Resolve an explicit ASS/SRT file, otherwise build a UTF-8 ASS."""
    raw = sheet.get("subtitle_ass") or sheet.get("subtitle_file") or sheet.get("subtitles")
    if raw:
        src = Path(raw)
        if not src.is_absolute():
            src = ROOT / src
        if src.exists():
            return str(src)
        raise FileNotFoundError(f"Subtitle file missing: {src}")
    return ensure_ass(sheet, out_path.stem)


def kinetic_text_path(sheet: dict, out_path: Path) -> str | None:
    """Build a standalone ASS overlay for source-anchored kinetic title cards."""
    cards = sheet.get("kinetic_text") or []
    if not cards:
        return None
    from aicut.kinetic_text import ass_lines

    events = ass_lines(cards, style=str(sheet.get("kinetic_text_style") or "clean"))
    ass = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        "Style: Title,Noto Sans SC,70,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text", *events,
    ]
    out = ROOT / "work" / f"{out_path.stem}.kinetic.ass"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(ass) + "\n", encoding="utf-8-sig")
    return str(out)


def build_command(sheet: dict, out_path: Path, with_subs: bool) -> list[str]:
    clips = sheet["clips"]
    inputs: list[str] = []
    filters: list[str] = []
    concat_labels: list[str] = []
    width, height, fps = int(sheet.get('width', W)), int(sheet.get('height', H)), int(sheet.get('fps', FPS))
    total = sum((float(c['end']) - float(c['start'])) / float(c.get('speed', 1)) for c in clips)

    for i, clip in enumerate(clips):
        src = resolve(clip["file"])
        inputs += ["-threads", "2", "-i", src]
        start, end = float(clip["start"]), float(clip["end"])
        speed = float(clip.get("speed") or 1.0)
        visual = ""
        if (clip.get("滤镜") or clip.get("filter")) in ("清晰明亮", "清晰", "明亮"):
            strength = max(0.0, min(float(clip.get("滤镜强度", clip.get("filter_intensity", 40.0))), 100.0)) / 100.0
            contrast = 1.0 + 0.20 * strength
            saturation = 1.0 + 0.16 * strength
            brightness = 0.04 * strength
            visual = (
                f",eq=contrast={contrast:.3f}:brightness={brightness:.3f}:"
                f"saturation={saturation:.3f},unsharp=5:5:0.35:5:5:0"
            )
        # Restrained, non-geometric beauty: outline-preserving softening plus
        # a small lift. Applied before captions/title cards; never reshapes faces.
        beauty = clip.get("美颜") or {}
        portrait = (sheet.get("render_pipeline") or {}).get("portrait_beauty") or {}
        # Face-local beauty is applied in the final tracked pass. Do not also
        # blur the entire frame in the clip renderer.
        if portrait.get("enabled"):
            beauty = {}
        if beauty:
            strength = max(0.0, min(float(beauty.get("strength", 20)), 40.0)) / 40.0
            radius = 0.8 + 0.8 * strength
            smooth = 0.12 + 0.12 * strength
            lift = 0.008 + 0.012 * strength
            visual += (f",smartblur=lr={radius:.3f}:ls={smooth:.3f}:lt=2:"
                       f"cr={radius:.3f}:cs={smooth:.3f}:ct=2"
                       f",eq=brightness={lift:.3f}:saturation=1.015")
        effect = str(clip.get("特效") or clip.get("effect") or "").strip()
        base_chain = (
            f"[{i}:v]trim=start={start:.3f}:end={end:.3f},setpts=(PTS-STARTPTS)/{speed:.4f},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black{visual},"
            f"setsar=1,fps={fps}"
        )
        if clip.get('keyframes'):
            from aicut.editor_capabilities import zoom_expression
            zoom = zoom_expression(clip['keyframes'], fps)
            base_chain += f",zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps={fps}"
        if effect == "光晕":
            # Portable approximation of Jianying's glow: mix a low-opacity,
            # softly blurred copy over the graded image before subtitles.
            filters.append(
                base_chain + f",format=yuv420p,split=2[base{i}][glow{i}];"
                f"[glow{i}]gblur=sigma=10:steps=2,format=rgba,colorchannelmixer=aa=0.16[glowa{i}];"
                f"[base{i}][glowa{i}]overlay=shortest=1,format=yuv420p[v{i}]"
            )
        else:
            filters.append(base_chain + f",format=yuv420p[v{i}]")
        length = (end - start) / speed
        if has_audio(src):
            filters.append(f'[{i}:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,'
                           f'{tempo_filters(speed)},volume={float(clip.get("volume", 1))},'
                           f'aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=duration={length:.6f}[a{i}]')
        else:
            filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={length:.6f}[a{i}]')
        concat_labels.append(f"[v{i}][a{i}]")

    n = len(clips)
    filters.append("".join(concat_labels) + f"concat=n={n}:v=1:a=1[vc][ac]")

    video_label = "vc"
    # Preserve source gain; a global +18 dB boost clips ordinary recordings.
    filters.append('[ac]alimiter=limit=0.95:level=false[an]')
    audio_label = "an"

    # B-roll 覆盖（静音视频层，按时间点叠在上面）
    for j, item in enumerate(sheet.get("broll") or []):
        src = resolve(item["file"])
        inputs += ["-threads", "2", "-i", src]
        idx = n + j
        s, e = float(item.get("start", 0)), float(item.get("end", 0))
        at = float(item.get("at", 0))
        dur = float(item.get("duration") or (e - s))
        if (not all(math.isfinite(x) for x in (s,e,at,dur)) or s<0 or e<=s or dur<=0 or at<0
                or at+dur>total+.05 or e>probe_duration(src)+.05):
            raise ValueError('B-roll source/timeline interval invalid')
        sp = (e-s)/dur
        filters.append(
            f"[{idx}:v]trim=start={s:.3f}:end={e:.3f},setpts=(PTS-STARTPTS)/{sp:.4f}+{at:.3f}/TB,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps}[b{j}]")
        filters.append(f"[{video_label}][b{j}]overlay=enable='between(t,{at:.3f},{at + dur:.3f})'"
                       f":eof_action=pass:repeatlast=0[o{j}]")
        video_label = f"o{j}"

    # Short, restrained full-frame flashes punctuate major spoken sections.
    for j, item in enumerate(sheet.get("visual_effects") or []):
        if item.get("type") != "flash":
            raise ValueError(f"unsupported portable visual effect: {item.get('type')}")
        start, end = float(item.get("start", 0)), float(item.get("end", 0))
        opacity = float(item.get("opacity", 0.28))
        if (not math.isfinite(start + end + opacity) or start < 0 or end <= start
                or end > total + .05 or end - start > .25 or not 0 <= opacity <= .5):
            raise ValueError("flash effect timing/opacity outside safe limits")
        label = f"flash{j}"
        filters.append(f"[{video_label}]drawbox=x=0:y=0:w=iw:h=ih:color=white@{opacity:.3f}:t=fill:"
                       f"enable='between(t,{start:.3f},{end:.3f})'[{label}]")
        video_label = label

    render_pipeline = sheet.get("render_pipeline") or {}
    raw_subs = sheet.get("subtitles_json")
    json_subs = False
    if raw_subs:
        subs_path = Path(raw_subs)
        if not subs_path.is_absolute():
            subs_path = ROOT / subs_path
        json_subs = subs_path.suffix.lower() == ".json" and subs_path.is_file()
    if with_subs and render_pipeline.get("subtitle_renderer") == "drawtext" and json_subs:
        from aicut.text_overlays import caption_filters
        overlays, video_label = caption_filters(sheet, out_path.stem, ROOT / "work", video_label, total)
        filters.extend(overlays)
    else:
        subtitle_file = subtitle_path(sheet, out_path) if with_subs else None
        if subtitle_file:
            esc = str(subtitle_file).replace("\\", "/").replace(":", "\\:")
            if Path(subtitle_file).suffix.lower() == ".ass":
                filters.append(f"[{video_label}]subtitles='{esc}'[vs]")
            else:
                filters.append(f"[{video_label}]subtitles='{esc}':force_style="
                               f"'FontName=Noto Sans SC,FontSize=30,PrimaryColour=&H00FFFFFF,"
                               f"OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
                               f"Alignment=2,MarginV=260'[vs]")
            video_label = "vs"

    # Render AI cut's portable kinetic title cards after subtitles. This is our
    # own ASS/libass renderer; it does not depend on JianYing effect assets.
    if render_pipeline.get("title_renderer") == "drawtext":
        from aicut.text_overlays import kinetic_filters
        overlays, video_label = kinetic_filters(sheet, out_path.stem, ROOT / "work", video_label, total)
        filters.extend(overlays)
    else:
        kinetic_file = kinetic_text_path(sheet, out_path)
        if kinetic_file:
            esc = kinetic_file.replace("\\", "/").replace(":", "\\:")
            filters.append(f"[{video_label}]subtitles='{esc}'[vk]")
            video_label = "vk"

    # BGM
    bgm = sheet.get("bgm") or {}
    if bgm.get("file"):
        src = resolve(bgm["file"])
        inputs += ['-stream_loop', '-1', '-i', src]
        idx = n + len(sheet.get("broll") or [])
        vol = float(bgm.get("volume", 0.15))
        filters.append(f"[{idx}:a]volume={vol:.3f},atrim=0:{total:.6f},asetpts=PTS-STARTPTS[bg]")
        filters.append(f"[{audio_label}][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[am]")
        audio_label = "am"

    vo = sheet.get('voiceover') or {}
    if vo.get('file'):
        if probe_duration(resolve(vo['file'])) > total + .1:
            raise ValueError('配音长于画面，停止导出以免截断句子')
        idx = n + len(sheet.get('broll') or []) + int(bool(bgm.get('file')))
        inputs += ['-i', resolve(vo['file'])]
        filters.append(f'[{idx}:a]volume={float(vo.get("volume", 1))},apad,atrim=duration={total:.6f}[vo]')
        filters.append(f'[{audio_label}][vo]amix=inputs=2:duration=first:normalize=0[av]')
        audio_label = 'av'
    sfx_events = sheet.get("sound_effects") or []
    sfx_labels = []
    sfx_base_index = (n + len(sheet.get("broll") or []) + int(bool(bgm.get("file")))
                      + int(bool(vo.get("file"))))
    for j, event in enumerate(sfx_events):
        src = resolve(event["file"])
        at = float(event.get("at", 0))
        volume = float(event.get("volume", 0.25))
        if not math.isfinite(at + volume) or at < 0 or at >= total or not 0 < volume <= 6.0:
            raise ValueError("sound effect timing/volume outside safe limits")
        idx = sfx_base_index + j
        inputs += ["-i", src]
        label = f"sfx{j}"
        delay_ms = round(at * 1000)
        filters.append(f"[{idx}:a]asetpts=PTS-STARTPTS,volume={volume:.3f},"
                       f"adelay=delays={delay_ms}:all=1,apad,atrim=duration={total:.6f}[{label}]")
        sfx_labels.append(f"[{label}]")
    if sfx_labels:
        filters.append(f"[{audio_label}]" + "".join(sfx_labels)
                       + f"amix=inputs={1 + len(sfx_labels)}:duration=first:normalize=0[sfxmix]")
        audio_label = "sfxmix"
    normalization = sheet.get('audio_normalization') or {}
    if normalization:
        target = float(normalization.get('integrated_lufs', -16))
        peak = float(normalization.get('true_peak_db', -1.5))
        if not math.isfinite(target+peak) or not -24 <= target <= -12 or not -6 <= peak <= -1:
            raise ValueError('audio normalization target outside supported range')
        filters.append(f'[{audio_label}]loudnorm=I={target}:TP={peak}:LRA=11[aloud]')
        audio_label = 'aloud'
    filters.append(f'[{audio_label}]alimiter=limit=0.95:level=false[afinal]')
    audio_label = 'afinal'

    graph = ";".join(filters)
    graph_args = ["-filter_complex", graph]
    if len(graph) > 12000:
        graph_path = ROOT / "work" / f"{out_path.stem}.filtergraph.txt"
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_path.write_text(graph, encoding="utf-8")
        graph_args = ["-filter_complex_script", str(graph_path)]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-filter_complex_threads", "2"] + inputs + graph_args + [
        "-map", f"[{video_label}]", "-map", f"[{audio_label}]",
        "-c:v", "libx264", "-threads", "4", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        '-t', f'{total:.6f}', "-movflags", "+faststart", str(out_path)]
    return cmd

def main() -> int:
    ap = argparse.ArgumentParser(description="成片导出（ffmpeg 渲染）")
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--out")
    ap.add_argument("--no-subs", action="store_true", help="不烧字幕")
    args = ap.parse_args()

    sheet_path = Path(args.sheet)
    if not sheet_path.is_absolute():
        sheet_path = ROOT / sheet_path
    sheet = load_sheet(sheet_path)
    from aicut.editor_capabilities import preflight
    try:
        preflight(sheet, 'jianying', 'portable')
    except ValueError as exc:
        print(f'[能力检查] {exc}')
        return 2
    import build_draft
    problems = build_draft.quality_gates(sheet)
    if problems:
        print('[质量门] ' + '; '.join(problems))
        return 2
    name = sheet.get("project") or sheet_path.stem
    project_name(name)
    out = Path(args.out) if args.out else ROOT / "07-output" / f"{name}.mp4"
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    staged = out.with_name(out.stem + '.' + uuid.uuid4().hex + '.partial.mp4')
    expected = sum((float(c['end']) - float(c['start'])) / float(c.get('speed', 1)) for c in sheet['clips'])
    render_sheet=sheet
    if len(sheet['clips'])>=6:
        from render_assembly import assemble
        render_sheet,assembly=assemble(sheet,ROOT,build_command,resolve)
        (ROOT/'work'/f'{out.stem}.assembly.json').write_text(json.dumps(assembly,ensure_ascii=False,indent=2),encoding='utf-8')
    cmd = build_command(render_sheet, staged, not args.no_subs)
    print(f"[导出] {len(sheet['clips'])} 个片段，B-roll {len(sheet.get('broll') or [])} 段 -> {out.name}")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    diagnostic = ROOT / 'work' / f'{out.stem}.export-diagnostic.json'
    diagnostic.write_text(json.dumps({'returncode':proc.returncode,'stderr':proc.stderr,'command':cmd},ensure_ascii=False,indent=2),encoding='utf-8')
    if proc.returncode != 0:
        print(f"[失败] ffmpeg 返回 {proc.returncode}，诊断日志 {diagnostic}，最后 400 字：")
        print((proc.stderr or "")[-400:])
        return 1

    portrait = (sheet.get("render_pipeline") or {}).get("portrait_beauty") or {}
    if portrait.get("enabled"):
        from aicut.portrait_beauty import apply_portrait_beauty
        face_stage = staged.with_name(staged.stem + ".face.partial.mp4")
        try:
            stats = apply_portrait_beauty(staged, face_stage, portrait, ffmpeg="ffmpeg")
            face_duration = probe_duration(str(face_stage))
            if abs(face_duration - expected) > .2 or not has_audio(str(face_stage)):
                raise RuntimeError("人脸美颜后成片时长/音轨校验失败")
            os.replace(face_stage, staged)
            report = ROOT / "work" / f"{out.stem}.portrait-beauty.json"
            report.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[人脸美颜] {stats['frames_processed']} 帧；人脸检测 {stats['detection_samples']} 次；"
                  f"平均置信度 {stats['mean_detection_confidence']:.3f}")
        except Exception as exc:  # noqa: BLE001
            print(f"[人脸美颜失败] {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
            return 1

    dur = probe_duration(str(staged))
    if abs(dur - expected) > .2:
        print(f'[失败] 导出时长 {dur} 与计划 {expected} 不一致，未替换旧文件')
        return 1
    os.replace(staged, out)
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"[完成] {out}  时长 {dur:.2f}s  体积 {size_mb:.1f}MB")
    if sheet.get('kinetic_text'):
        print(f"[包装] 已渲染 {len(sheet['kinetic_text'])} 组动态花字；剪映专有特效素材不在此渲染器中复现。")
    else:
        print("提示：这是 ffmpeg 渲染版；剪映专有特效与文字特效素材需在剪映中导出。")
    # Run the decoration-stage frame sample and a separate reproducible final
    # sample only after the MP4 has been atomically placed at its final path.
    try:
        from review_render import review_video
        report = review_video(out, sheet_path)
        print(f"[导出后复审] 花字/贴纸抽查 + 随机终稿抽查已完成：{report}")
    except Exception as exc:  # noqa: BLE001
        print(f"[导出后复审未完成] {type(exc).__name__}: {str(exc)[:180]}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
