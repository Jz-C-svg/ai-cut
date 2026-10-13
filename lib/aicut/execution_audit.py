"""Check requested timeline values against serialized native draft values."""
from pathlib import Path
import math
from .keyframe_execution import encode_keyframes
from .effect_execution import compile_effect, compile_filter, native_picture_config


def _same_value(expected, actual):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and math.isfinite(actual) and abs(expected - actual) <= 1e-9)
    if isinstance(expected, dict):
        return isinstance(actual, dict) and expected.keys() == actual.keys() and all(
            _same_value(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            _same_value(a, b) for a, b in zip(expected, actual))
    return expected == actual


def _time_matches(expected, actual):
    return isinstance(actual, dict) and all(isinstance(actual.get(key), (int, float))
        and not isinstance(actual.get(key), bool) and math.isfinite(actual[key])
        and abs(actual[key] - value) <= 2 for key, value in expected.items())


def _effect_controls_equal(expected, actual):
    """Compare applied controls; native saves may strip catalog-only bounds.

    Name/value identify execution. Retained port/index fields must still match;
    missing min/max/default metadata does not mean a changed applied value.
    """
    if not isinstance(actual, list) or len(expected) != len(actual):
        return False
    if any(not isinstance(item, dict) for item in actual):
        return False
    names = [item.get('name') for item in actual]
    if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
        return False
    by_name = {item.get('name'): item for item in actual}
    for wanted in expected:
        found = by_name.get(wanted.get('name'), {})
        if not _same_value(wanted.get('value'), found.get('value')):
            return False
        for key in ('portIndex', 'parameterIndex'):
            if key in found and not _same_value(wanted.get(key), found[key]):
                return False
    return True


def audit_timeline(sheet, data, resolve):
    tracks = data.get("tracks") or []
    main = next((t for t in tracks if t.get("name") == "main"), {})
    segments = main.get("segments") or []
    clips = sheet.get("clips") or []
    materials = {m["id"]: m for group in (data.get("materials") or {}).values()
                 for m in group if isinstance(m, dict) and m.get("id")}
    rows = []

    def check(name, ok, expected=None, actual=None):
        rows.append({"field": name, "status": "applied" if ok else "partial_or_missing",
                     "expected": expected, "actual": actual})

    check("main.segment_count", len(segments) == len(clips), len(clips), len(segments))
    cursor = 0.0
    for i, (clip, seg) in enumerate(zip(clips, segments)):
        source = float(clip["end"]) - float(clip["start"])
        speed = float(clip.get("speed", 1))
        length = source / speed
        expected = {"start": round(cursor * 1e6), "duration": round(length * 1e6)}
        actual = seg.get("target_timerange") or {}
        check(f"clips[{i}].target", _time_matches(expected, actual), expected, actual)
        expected = {"start": round(float(clip["start"]) * 1e6), "duration": round(source * 1e6)}
        actual = seg.get("source_timerange") or {}
        check(f"clips[{i}].source", _time_matches(expected, actual), expected, actual)
        for key, expected in (("speed", speed), ("volume", float(clip.get("volume", 1)))):
            actual = seg.get(key)
            check(f"clips[{i}].{key}", isinstance(actual, (float, int)) and abs(actual - expected) < 1e-6, expected, actual)
        expected_path = str(Path(resolve(clip["file"])).resolve())
        raw_path = materials.get(seg.get("material_id"), {}).get("path")
        actual_path = str(Path(raw_path).resolve()) if raw_path else None
        check(f"clips[{i}].material", actual_path == expected_path, expected_path, actual_path)
        def normalized(groups):
            return sorted(((g.get("property_type"), k.get("time_offset"), k.get("values"),
                           k.get("curveType"), k.get("left_control"), k.get("right_control"))
                          for g in groups for k in g.get("keyframe_list") or []), key=repr)
        expected_kf = normalized([g.export_json() for g in encode_keyframes(clip.get("keyframes") or [], round(length * 1e6))])
        actual_kf = normalized(seg.get("common_keyframes") or [])
        check(f"clips[{i}].keyframes", expected_kf == actual_kf, expected_kf, actual_kf)
        cursor += length
    for key, track_name, default in (("bgm", "bgm", .15), ("voiceover", "voiceover", 1)):
        config = sheet.get(key) or {}
        if not config.get("file"):
            continue
        found = [s for t in tracks if t.get("name") == track_name for s in t.get("segments") or []]
        expected = float(config.get("volume", default))
        actual = [s.get("volume") for s in found]
        check(f"{key}.volume", bool(found) and all(isinstance(v, (int, float)) and abs(v - expected) < 1e-6 for v in actual), expected, actual)
    # Counts alone can pass with the wrong resource, time range or control value.
    # Follow every decoration segment's material_id and check the native payload.
    expected_decorations = {'effect': [], 'filter': []}
    picture = native_picture_config(sheet)
    cursor = 0.0
    for i, clip in enumerate(clips):
        length = (float(clip['end']) - float(clip['start'])) / float(clip.get('speed', 1))
        timerange = {'start': round(cursor * 1e6), 'duration': round(length * 1e6)}
        for kind, compile_item in (('effect', lambda: compile_effect(clip)),
                                   ('filter', lambda: compile_filter(clip, picture))):
            try:
                item = compile_item()
            except (TypeError, ValueError) as exc:
                check(f'clips[{i}].{kind}.request', False, 'valid native resource and controls', str(exc))
                continue
            if item:
                expected_decorations[kind].append((i, timerange, item))
        cursor += length
    for kind, expected_items in expected_decorations.items():
        actual_items = [s for t in tracks if t.get('name') == kind or t.get('type') == kind
                        for s in t.get('segments') or []]
        actual_items.sort(key=lambda s: (s.get('target_timerange') or {}).get('start', -1))
        check(f'{kind}.segment_count', len(actual_items) == len(expected_items), len(expected_items), len(actual_items))
        for (i, timerange, item), seg in zip(expected_items, actual_items):
            label = f'clips[{i}].{kind}'
            actual_range = seg.get('target_timerange') or {}
            check(f'{label}.target', all(isinstance(actual_range.get(k), (int, float))
                  and abs(actual_range[k] - value) <= 2 for k, value in timerange.items()), timerange, actual_range)
            material = materials.get(seg.get('material_id'), {})
            for field in ('resource_id', 'effect_id'):
                expected = str(getattr(item.member.value, field))
                actual = material.get(field)
                check(f'{label}.{field}', actual is not None and str(actual) == expected, expected, actual)
            if kind == 'effect':
                expected = item.adjust_params
                actual = material.get('adjust_params')
                check(f'{label}.adjust_params', _effect_controls_equal(expected, actual), expected, actual)
            else:
                expected = item.intensity / 100
                actual = material.get('value')
                check(f'{label}.value', _same_value(expected, actual), expected, actual)
    return {"status": "complete" if all(r["status"] == "applied" for r in rows) else "needs_review",
            "checks": rows, "host_render_verified": False}
