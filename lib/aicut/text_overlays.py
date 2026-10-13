"""Windows-font-backed subtitle and kinetic-title drawtext overlays."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path


def filter_path(path: str | Path) -> str:
    """Quote a path for an FFmpeg filter option (not for a shell)."""
    value = str(path).replace("\\", "/")
    value = value.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")
    return f"'{value}'"


def clean_text(value: object) -> str:
    text = str(value or "").replace("\r", "").replace("\x00", "")
    # Drawtext text files use expansion=none, but strip format braces as a
    # second guard against model supplied filter-like content.
    return text.replace("{", "（").replace("}", "）")


def wrap_caption(value: object, max_chars: int = 12) -> str:
    """Wrap Chinese captions without erasing existing ASR phrase breaks."""
    text = clean_text(value)
    if not 4 <= max_chars <= 24:
        raise ValueError("caption max_chars must be between 4 and 24")
    lines: list[str] = []
    for phrase in text.splitlines() or [text]:
        phrase = phrase.strip()
        while len(phrase) > max_chars:
            chunk_count = math.ceil(len(phrase) / max_chars)
            target = math.ceil(len(phrase) / chunk_count)
            lower = max(4, target - 3)
            upper = min(max_chars, target + 3)
            protected = list(re.finditer(
                r"\d+(?:\.\d+)?(?:毫升|毫克|厘米|毫米|公斤|千克|克|度|月|码|个|件|片|双)?|[A-Za-z]+(?:码)?|"
                r"预产期|宝妈们|宝宝|物品清单|一小瓶|奶粉|奶瓶|奶嘴|硅胶勺|"
                r"乳头混淆|吃辅食|奶瓶刷|恒温壶|大牌子|打水|做好规划|"
                r"准备|收藏|SS码|S码",
                phrase,
            ))
            candidates = [pos for pos in range(lower, upper + 1)
                          if not any(m.start() < pos < m.end() for m in protected)
                          and phrase[pos - 1] not in "的地得了着和与或把被从在为"
                          and phrase[pos] not in "的地得了着和与或把被从在为"]
            # Prefer phrase-level endings; use a balanced split if no lexical cue fits.
            cues = ("给大家", "请注意", "可以收藏", "准备", "奶粉", "奶瓶", "奶嘴", "容量",
                    "物品清单", "硅胶勺", "宝宝", "造成", "乳头混淆", "辅食", "奶瓶刷",
                    "恒温壶", "大牌子", "打水", "控制", "做好规划", "最好")
            preferred = [pos for pos in candidates if any(phrase[:pos].endswith(cue) for cue in cues)
                         or re.search(r"\d+(?:\.\d+)?(?:毫升|毫克|厘米|毫米|公斤|千克|克|度|月|码|个|件|片|双)$", phrase[:pos])]
            pool = preferred or candidates
            cut = min(pool, key=lambda pos: abs(pos - target)) if pool else target
            lines.append(phrase[:cut].rstrip())
            phrase = phrase[cut:].lstrip()
        if phrase:
            lines.append(phrase)
    return "\n".join(lines)


def split_caption_timeline(
    segments: list[dict], max_chars: int = 12, gap: float = 0.035,
    minimum_part: float = 0.35,
) -> list[dict]:
    """Turn visual phrase lines into sequential single-line subtitle events.

    Word-level times are unavailable in this transcript, so split times are
    estimated by character weight inside each original ASR interval. Original
    ASR timing is retained in provenance fields for review.
    """
    if gap < 0 or minimum_part <= 0:
        raise ValueError("subtitle gap/minimum duration must be positive")
    result: list[dict] = []
    for index, raw in enumerate(segments):
        item = dict(raw)
        start, end = _time(item["start"]), _time(item["end"])
        if end <= start:
            raise ValueError("subtitle segment end must be after start")
        # Source transcripts may already contain layout-only line breaks. Flatten
        # those before making one-line timed events so numbers and phrases are
        # reflowed as a unit rather than inherited as broken subtitle fragments.
        source_text = clean_text(item.get("text", "")).replace("\n", "").replace("\r", "")
        if 'caption_phrases' in item:
            from .learning_execution import phrase_parts
            lines = phrase_parts(source_text, item['caption_phrases'], max_chars)
        else:
            lines = [line.strip() for line in wrap_caption(source_text, max_chars).splitlines() if line.strip()]
        if not lines:
            continue
        if len(lines) == 1:
            item["text"] = lines[0]
            result.append(item)
            continue
        duration = end - start
        available = duration - gap * (len(lines) - 1)
        weights = [max(1, len(line)) for line in lines]
        if available / len(lines) < minimum_part:
            raise ValueError(f"ASR interval too short for sequential subtitle parts: {start:.3f}-{end:.3f}")
        cursor = start
        for part, (line, weight) in enumerate(zip(lines, weights), 1):
            part_end = end if part == len(lines) else cursor + available * weight / sum(weights)
            if part_end - cursor < minimum_part:
                raise ValueError('subtitle phrase duration below minimum; revise phrasing or alignment')
            part_item = dict(item)
            # Timed events are already split; later consumers must not split the
            # original full phrase list against one short event a second time.
            part_item.pop('caption_phrases', None)
            part_item.update({
                "start": round(cursor, 3), "end": round(part_end, 3), "text": line,
                "caption_group": index, "caption_part": part, "caption_parts": len(lines),
                "timing_method": "phrase-weighted estimate within original ASR interval",
                "phrase_origin": "model_source_bound" if 'caption_phrases' in item else "rule_layout",
            })
            result.append(part_item)
            cursor = part_end + gap
    return result


def _write_text(work: Path, stem: str, index: int, text: str) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    target = work / f"{stem}.{index:03d}.txt"
    target.write_text(clean_text(text), encoding="utf-8", newline="")
    return target


def _time(value: object) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("text overlay time must be finite")
    return result


def caption_filters(
    sheet: dict, stem: str, work: Path, current: str, total: float
) -> tuple[list[str], str]:
    raw = sheet.get("subtitles_json")
    if not raw:
        return [], current
    source = Path(raw)
    if not source.is_absolute():
        source = work.parent / source
    if source.suffix.lower() != ".json" or not source.is_file():
        raise ValueError("drawtext 字幕模式需要存在的 JSON 逐字稿")
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    layout = sheet.get("subtitle_layout") or {}
    pipeline = sheet.get("render_pipeline") or {}
    font = Path(pipeline.get("font_path") or r"C:\Windows\Fonts\msyhbd.ttc")
    if not font.is_file():
        raise FileNotFoundError(f"字幕字体不存在: {font}")
    size = int(layout.get("font_size_px", 60))
    y = int(layout.get("top_px", 1040))
    if not 32 <= size <= 100 or not 250 <= y <= 1600:
        raise ValueError("字幕字号或位置超出安全范围")
    box_alpha = float(layout.get("box_alpha", 0.70))
    max_chars = int(layout.get("max_chars_per_line", 12))
    if not 0 <= box_alpha <= 1:
        raise ValueError("字幕底板透明度超出 0..1")

    filters: list[str] = []
    segments = data.get("segments", [])
    if layout.get("single_line_per_caption"):
        segments = split_caption_timeline(segments, max_chars=max_chars)
    for i, segment in enumerate(segments):
        start, end = _time(segment["start"]), _time(segment["end"])
        if start < 0 or end <= start or end > total + 0.10:
            raise ValueError(f"字幕时间区间无效: {start:.3f}..{end:.3f}")
        caption_text = str(segment["text"]).replace("\n", " ") if layout.get("single_line_per_caption") else wrap_caption(segment["text"], max_chars)
        path = _write_text(work, f"{stem}.caption", i, caption_text)
        nxt = f"cap{i}"
        # FFmpeg between() includes both ends. Pull the right edge back slightly
        # so adjacent single-line events cannot coexist on an exact frame.
        display_end = max(start, end - 0.025) if layout.get("single_line_per_caption") else end
        opts = (
            f"fontfile={filter_path(font)}:textfile={filter_path(path)}:expansion=none:"
            f"fontsize={size}:fontcolor=white:borderw=2:bordercolor=black@0.72:"
            f"box=1:boxcolor=0x0A0E17@{box_alpha:.3f}:boxborderw=8:"
            f"line_spacing=5:x=(w-text_w)/2:y={y}:fix_bounds=1:"
            f"enable='between(t,{start:.3f},{display_end:.3f})'"
        )
        filters.append(f"[{current}]drawtext={opts}[{nxt}]")
        current = nxt
    return filters, current


def kinetic_filters(
    sheet: dict, stem: str, work: Path, current: str, total: float
) -> tuple[list[str], str]:
    cards = sheet.get("kinetic_text") or []
    if not cards:
        return [], current
    style = str(sheet.get("kinetic_text_style") or "clean")
    accents = {
        "clean": "0xB8D77F", "festival": "0xFFCA50",
        "pop": "0xFF633C", "minimal": "0xFFFFFF",
    }
    if style not in accents:
        raise ValueError(f"unknown kinetic text style: {style}")
    accent = accents[style]
    pipeline = sheet.get("render_pipeline") or {}
    font = Path(pipeline.get("font_path") or r"C:\Windows\Fonts\msyhbd.ttc")
    if not font.is_file():
        raise FileNotFoundError(f"花字字体不存在: {font}")
    filters: list[str] = []

    for i, card in enumerate(cards):
        start, end = _time(card["start"]), _time(card["end"])
        if start < 0 or end <= start or end > total + 0.10:
            raise ValueError(f"花字时间区间无效: {start:.3f}..{end:.3f}")
        title = " ".join(part for part in (
            clean_text(card.get("label", "")), clean_text(card.get("title", ""))
        ) if part)
        subtitle = clean_text(card.get("subtitle", ""))
        if not title:
            continue
        base = f"kt{i}"
        # Adjacent cards share a boundary, not a frame. FFmpeg between() is
        # inclusive at both ends and would draw both titles on that frame.
        enable = f"gte(t,{start:.3f})*lt(t,{end:.3f})"
        filters.append(f"[{current}]null[{base}p]")
        stripe = f"{base}s"
        filters.append(
            f"[{base}p]drawbox=x=60:y=163:w=9:h=156:color={accent}@1:t=fill:"
            f"enable='{enable}'[{stripe}]"
        )

        title_path = _write_text(work, f"{stem}.title", i, title)
        animation = card.get("animation", "slide")
        fade_in = max(0.08, min(0.28, float(card.get("fade_in", 0.18))))
        if animation == "slide":
            x_expr = f"88+18*(1-min(1\\,max(0\\,(t-{start:.3f})/{fade_in:.3f})))"
            size_expr = str(min(78 if card.get("kind") == "title" else 70,
                                max(36, int(880 / max(1, len(title))))))
        elif animation == "fade":
            x_expr = "88"
            size_expr = str(min(78 if card.get("kind") == "title" else 70,
                                max(36, int(880 / max(1, len(title))))))
        elif animation == "pop":
            x_expr = f"(w-text_w)/2+14*(1-min(1\\,max(0\\,(t-{start:.3f})/{fade_in:.3f})))"
            size = min(78 if card.get("kind") == "title" else 70,
                       max(36, int(880 / max(1, len(title)))))
            size_expr = str(size)
        else:
            raise ValueError(f"unsupported kinetic title animation: {animation}")
        alpha_value = "0.92" if animation == "fade" else "1"
        effect = card.get("effect", "none")
        stroke_w = 5 if effect == "glow" else 3 if effect == "outline" else 1
        # Keep overlays crisp; subtitle/title direction explicitly rejects shadows.
        title_label = f"{base}t"
        filters.append(
            f"[{stripe}]drawtext=fontfile={filter_path(font)}:textfile={filter_path(title_path)}:"
            f"expansion=none:fontsize='{size_expr}':fontcolor={accent}:"
            f"borderw={stroke_w}:bordercolor=0x000000@0.95:shadowcolor=black@0:"
            f"shadowx=0:shadowy=0:x='{x_expr}':y=169:alpha={alpha_value}:"
            f"enable='{enable}'[{title_label}]"
        )
        current = title_label
        if subtitle:
            subtitle_path = _write_text(work, f"{stem}.title-sub", i, subtitle)
            sub_label = f"{base}u"
            filters.append(
                f"[{current}]drawtext=fontfile={filter_path(font)}:textfile={filter_path(subtitle_path)}:"
                f"expansion=none:fontsize=34:fontcolor=white:borderw=2:bordercolor=black@0.9:"
                f"shadowcolor=black@0:shadowx=0:shadowy=0:x=90:y=270:"
                f"alpha=1:enable='{enable}'[{sub_label}]"
            )
            current = sub_label
    return filters, current
