"""Conservative delivery capabilities: file generation is never host execution."""
import math
from .keyframe_execution import encode_keyframes, ALIASES
from .effect_execution import compile_effect, compile_filter, filter_settings, native_picture_config

EDITORS = {"jianying": "剪映专业版", "capcut": "CapCut", "premiere": "Premiere Pro",
           "davinci": "DaVinci Resolve", "finalcut": "Final Cut Pro", "vegas": "VEGAS Pro",
           "aftereffects": "After Effects"}


def capabilities():
    return [{"id": key, "name": name,
             "project_delivery": "excluded_compatibility_component_required" if key in {"jianying", "capcut"} else "requires_adapter_validation",
             "native_auto_render": False, "host_verified": False,
             "portable_render": False, "portable_engine": "ffmpeg",
             "portable_status": "source_present_but_entry_point_dependency_separation_not_validated"}
            for key, name in EDITORS.items()]


def preflight(sheet, software, engine):
    if software not in EDITORS:
        raise ValueError(f"Unknown editor: {software}")
    if engine not in {"project", "portable", "native"}:
        raise ValueError(f"Unknown delivery engine: {engine}")
    if engine == "native":
        raise ValueError(f"{EDITORS[software]}: native automatic export has not been verified; choose project or portable explicitly")
    if engine == "project" and software not in {"jianying", "capcut"}:
        raise ValueError(f"{EDITORS[software]}: current adapter is not a verified native project writer; delivery blocked instead of emitting a placeholder project")
    errors = []
    picture = native_picture_config(sheet) if engine == 'project' else (sheet.get('画面') or {})
    if engine == 'project':
        for key in ('sound_effects', 'audio_normalization', 'subtitle_file', 'subtitle_ass', 'self_contained_assets'):
            if sheet.get(key):
                errors.append(f'{key}: native sheet writer has no verified consumer')
        if ((sheet.get('render_pipeline') or {}).get('portrait_beauty') or {}).get('enabled'):
            errors.append('portrait_beauty: native writer cannot apply tracked face postprocessing')
    for i, clip in enumerate(sheet.get("clips") or []):
        start, end, speed = float(clip['start']), float(clip['end']), float(clip.get('speed', 1))
        if not all(math.isfinite(v) for v in (start, end, speed)) or start < 0 or end <= start or speed <= 0:
            raise ValueError(f'clips[{i}]: invalid trim or speed')
        duration_us = round((end - start) / speed * 1e6)
        try:
            encode_keyframes(clip.get("keyframes") or [], duration_us)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"clips[{i}].keyframes: {exc}")
        try:
            compile_effect(clip)
            if engine == 'project':
                compile_filter(clip, picture)
            else:
                filter_settings(clip, picture)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f'clips[{i}].decoration: {exc}')
        if engine == "portable":
            if 'effect_params' in clip or '特效参数' in clip:
                errors.append(f'clips[{i}]: portable renderer cannot reproduce native effect_params / 特效参数')
            if picture:
                errors.append(f'clips[{i}]: portable renderer has no consumer for sheet.画面; specify supported clip filters explicitly')
            if clip.get("转场调用名") or clip.get("transition"):
                errors.append(f"clips[{i}]: native transition requires editor export")
            fx = clip.get("特效") or clip.get("effect")
            filt = clip.get("滤镜") or clip.get("filter")
            if fx and fx != "光晕":
                errors.append(f"clips[{i}]: unsupported portable effect {fx}")
            if filt and filt not in {"清晰明亮", "清晰", "明亮"}:
                errors.append(f"clips[{i}]: unsupported portable filter {filt}")
            for kf in clip.get("keyframes") or []:
                if ALIASES.get(str(kf.get("property", "")).lower()) != "uniform_scale" or str(kf.get("easing", "Line")).lower() != "line" or float(kf["value"]) < 1:
                    errors.append(f"clips[{i}]: portable motion supports centered scale >=1 with Line easing only")
        elif clip.get('美颜'):
            errors.append(f'clips[{i}].美颜: native writer has no verified consumer')
    if engine == "portable":
        if ((sheet.get('render_pipeline') or {}).get('portrait_beauty') or {}).get('enabled'):
            errors.append('portrait_beauty requires the tracked postprocessing pipeline; portable delivery cannot skip it')
        for key in ("花字", "style", "subtitle_layout", "visual_plan"):
            # Layout/visual-plan have dedicated portable consumers; native presets do not.
            if key in {"花字", "style"} and sheet.get(key):
                errors.append(f"{key}: native resource/preset cannot be reproduced by the portable renderer")
        card_texts = {str(c.get("title")) for c in sheet.get("kinetic_text") or []}
        for key in ("cover_text", "cta"):
            if sheet.get(key) and str(sheet[key]) not in card_texts:
                errors.append(f"{key} must be represented by a timed kinetic_text card for portable rendering")
    if errors:
        raise ValueError("; ".join(errors))


def zoom_expression(keyframes, fps):
    points = sorted((float(k["t"]) * fps, float(k["value"])) for k in keyframes)
    if not points:
        return "1"
    expr = f"{points[-1][1]:.9g}"
    for (a, va), (b, vb) in reversed(list(zip(points, points[1:]))):
        linear = f"({va:.9g}+({vb-va:.9g})*(on-{a:.9g})/{b-a:.9g})"
        expr = f"if(lt(on,{b:.9g}),{linear},{expr})"
    return f"if(lt(on,{points[0][0]:.9g}),{points[0][1]:.9g},{expr})"
