"""Shared, strict keyframe encoding for draft generation and readback checks."""
import math

ALIASES = {"scale": "uniform_scale", "uniform_scale": "uniform_scale",
           "x": "position_x", "position": "position_x", "position_x": "position_x",
           "y": "position_y", "position_y": "position_y", "rotation": "rotation",
           "opacity": "alpha", "alpha": "alpha", "volume": "volume"}
EASING = {"line": "Line", "hold": "Hold", "smooth": "Smooth", "beizer": "Beizer"}


def encode_keyframes(items, duration_us):
    from JianYingDraft.keyframe import Keyframe, KeyframeList, KeyframeProperty
    groups = {}
    if not isinstance(items, list):
        raise ValueError("keyframes must be an array")
    for item in items:
        prop = ALIASES.get(str(item.get("property", "")).strip().lower())
        if prop is None:
            raise ValueError(f"Unsupported keyframe property: {item.get('property')}")
        t, value = float(item.get("t", 0)), float(item["value"])
        if not math.isfinite(t) or not math.isfinite(value) or not 0 <= round(t * 1e6) <= duration_us:
            raise ValueError("Keyframe time/value invalid or outside segment")
        if prop == "uniform_scale" and value <= 0 or prop == "alpha" and not 0 <= value <= 1:
            raise ValueError("Keyframe scale/alpha outside supported range")
        easing = EASING.get(str(item.get("easing", "Line")).lower())
        if easing is None:
            raise ValueError("Unsupported easing (free curves require a registered graph)")
        offset = round(t * 1e6)
        group = groups.setdefault(prop, KeyframeList(getattr(KeyframeProperty, prop)))
        if any(k.time_offset == offset for k in group.keyframes):
            raise ValueError("Duplicate keyframe time on the same property")
        kf = Keyframe(offset, value)
        if easing in {"Smooth", "Beizer"}:
            for key in ("control_in", "control_out"):
                point = item.get(key)
                if point is not None:
                    values = (point.get("x"), point.get("y")) if isinstance(point, dict) else point
                    if len(values) != 2 or not all(math.isfinite(float(v)) for v in values):
                        raise ValueError("Invalid easing control point")
            kf.set_bezier(item.get("control_in"), item.get("control_out"), curve_type=easing)
        else:
            kf._curve_type = easing
        group.keyframes.append(kf)
    for group in groups.values():
        group.keyframes.sort(key=lambda k: k.time_offset)
    return list(groups.values())


def apply_keyframes(segment, clip, duration_us):
    groups = encode_keyframes(clip.get("keyframes") or [], duration_us)
    segment.common_keyframes.extend(groups)
    return sum(len(g.keyframes) for g in groups)
