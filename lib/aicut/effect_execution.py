"""Compile named native effect controls without guessing a generic intensity.

Effect controls are percentages (0..100) keyed by the exact metadata names.
The native library maps these to each control's actual min/max range. Missing
controls retain native defaults; an absent parameter object keeps old behavior.
"""
from dataclasses import dataclass
import json
import math
from numbers import Real
from pathlib import Path

def _native_types():
    """Load the externally supplied editor compatibility component on demand."""
    try:
        from JianYingDraft import FilterType, VideoCharacterEffectType, VideoSceneEffectType
    except ImportError as exc:
        raise ValueError('Native effects require the excluded JianYingDraft compatibility component; this source release has no validated replacement.') from exc
    return FilterType, VideoCharacterEffectType, VideoSceneEffectType


def native_picture_config(sheet, preset=None):
    """Use the same native preset defaults in preflight, generation and audit."""
    if preset is None:
        path = Path(__file__).resolve().parents[2] / 'config' / 'text_styles.json'
        presets = json.loads(path.read_text(encoding='utf-8-sig'))['presets']
        templates = {'koubo-standard': '口播标准', 'fangxing-showcase': '房型展示', 'duibi-ceping': '口播标准'}
        name = sheet.get('style') or templates.get(sheet.get('template'), '口播标准')
        preset = presets.get(name) or presets['口播标准']
    overrides = sheet.get('画面') or {}
    if not isinstance(overrides, dict):
        raise ValueError('sheet.画面 must be an object')
    picture = {**(preset.get('画面') or {}), **overrides}
    for native, english in (('滤镜', 'filter'), ('滤镜强度', 'filter_intensity')):
        if native in overrides or english in overrides:
            value = aliased_value(overrides, native, english)
            picture.pop(english, None)
            picture[native] = value
    return picture


def aliased_value(config, native, english, default=None):
    if native in config and english in config and config[native] != config[english]:
        raise ValueError(f"conflicting aliases: {native} / {english}")
    return config[native] if native in config else config.get(english, default)


def percentage(value, label):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label}: expected a finite number in 0..100")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError(f"{label}: expected a finite number in 0..100")
    return number


@dataclass(frozen=True)
class CompiledEffect:
    name: str
    member: object
    params: list | None
    adjust_params: list


@dataclass(frozen=True)
class CompiledFilter:
    name: str
    member: object
    intensity: float


def compile_effect(clip):
    name = aliased_value(clip, '特效', 'effect')
    supplied = '特效参数' in clip or 'effect_params' in clip
    controls = aliased_value(clip, '特效参数', 'effect_params')
    if not name:
        if supplied:
            raise ValueError('effect_params / 特效参数 requires an effect')
        return None
    if not isinstance(name, str):
        raise ValueError('effect must be a native effect name')
    _, VideoCharacterEffectType, VideoSceneEffectType = _native_types()
    member = next((cls.__members__.get(name) for cls in
                   (VideoSceneEffectType, VideoCharacterEffectType)
                   if name in cls.__members__), None)
    if member is None:
        raise ValueError(f'unknown native effect: {name}')
    params = None
    if supplied:
        if not isinstance(controls, dict):
            raise ValueError('effect_params / 特效参数 must be an object keyed by native parameter names')
        names = [param.name for param in member.value.params]
        unknown = set(controls).difference(names)
        if unknown:
            raise ValueError(f'{name}: unknown effect parameter(s): {sorted(unknown, key=str)}; allowed: {names}')
        params = [percentage(controls[key], f'{name}.{key}') if key in controls else None for key in names]
    adjusted = [param.export_json() for param in member.value.parse_params(params)]
    return CompiledEffect(name, member, params, adjusted)


def filter_settings(clip, picture=None):
    picture = picture or {}
    name = aliased_value(clip, '滤镜', 'filter') or aliased_value(picture, '滤镜', 'filter')
    fallback = aliased_value(picture, '滤镜强度', 'filter_intensity', 50)
    intensity = aliased_value(clip, '滤镜强度', 'filter_intensity', fallback)
    explicit = '滤镜强度' in clip or 'filter_intensity' in clip
    if not name:
        if explicit or '滤镜强度' in picture or 'filter_intensity' in picture:
            raise ValueError('filter_intensity / 滤镜强度 requires a filter')
        return None, None
    if not isinstance(name, str):
        raise ValueError('filter must be a native filter name')
    return name, percentage(intensity, f'{name}.filter_intensity')


def compile_filter(clip, picture=None):
    name, intensity = filter_settings(clip, picture)
    if not name:
        return None
    FilterType, _, _ = _native_types()
    member = FilterType.__members__.get(name)
    if member is None:
        raise ValueError(f'unknown native filter: {name}')
    return CompiledFilter(name, member, intensity)
