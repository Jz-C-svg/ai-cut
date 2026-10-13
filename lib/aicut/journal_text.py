"""Cream memo lettering: restrained shared palette, parameter hierarchy and drawn marks."""
import math
import re


def ass_lines(cards):
    from .kinetic_text import clean, tc
    lines = []
    ink, green, cream, rose = '45392C', '736D37', 'EAF5FC', '6662A8'
    for index, card in enumerate(cards):
        start, end = float(card['start']), float(card['end'])
        if not math.isfinite(start + end) or start < 0 or end <= start:
            raise ValueError('invalid memo interval')
        title = str(card['title'])
        if not title or len(title) > 16:
            raise ValueError('memo title must contain 1..16 characters')
        warning = card.get('variant') == 'warning'
        accent = rose if warning else green
        def event(layer, tags, text):
            lines.append(f'Dialogue: {layer},{tc(start)},{tc(end)},Title,,0,0,0,,{{{tags}}}{text}')
        # Shared 200 ms upward entrance for every component, no competing bounce.
        def motion(x, y):
            return rf'\move({x},{y+14},{x},{y},0,200)\fad(120,160)'
        panel = r'm 18 0 l 882 0 b 893 0 900 7 900 18 l 900 216 b 900 227 893 234 882 234 l 18 234 b 7 234 0 227 0 216 l 0 18 b 0 7 7 0 18 0{\p0}'
        event(1, rf'\an7{motion(95,85)}\p1\bord0\shad0\1c&H{ink}&\1a&H90&', panel)
        event(2, rf'\an7{motion(90,78)}\p1\bord0\shad0\1c&H{cream}&', panel)
        # Paper tape and ruled label band.
        event(3, rf'\an7{motion(370,65)}\p1\bord0\shad0\1c&HBDCFB7&\1a&H30&',
              r'm 0 0 l 330 7 l 321 35 l 5 28{\p0}')
        event(3, rf'\an7{motion(124,116)}\p1\bord0\shad0\1c&H{accent}&',
              r'm 0 0 l 6 0 l 6 34 l 0 34{\p0}')
        event(4, rf'\an7{motion(148,112)}\fnMicrosoft YaHei\fs32\b1\bord0\shad0\1c&H{accent}&', clean(card.get('label','清单笔记')))
        event(4, rf'\an9{motion(950,112)}\fnMicrosoft YaHei\fs26\b0\bord0\shad0\1c&H{green}&', '待产 / 衣物')
        # Only one primary emphasis method: large key text; numbers get the largest size.
        numeric = bool(re.search(r'\d', title))
        size = 112 if numeric else 82
        if len(title) > 11:
            size = 82
        event(4, rf'\an5{motion(540,216)}\fnMicrosoft YaHei\fs{size}\b1\bord0\shad0\1c&H{accent}&', clean(title))
        if warning:
            event(5, rf'\an7{motion(210,162)}\p1\bord2\3c&H{rose}&\shad0\1a&HFF&',
                  r'm 0 50 b -12 -7 585 -26 650 39 b 692 107 36 139 0 50 m 9 57 b 15 2 606 -13 656 45{\p0}')
        else:
            event(3, rf'\an7{motion(180,275)}\p1\bord0\shad0\1c&HBDCFB7&',
                  r'm 0 0 l 714 -3 l 710 3 l 0 5{\p0}')
        if card.get('subtitle'):
            event(5, rf'\an5{motion(540,294)}\fnMicrosoft YaHei\fs26\b0\bord0\shad0\1c&H{ink}&', clean(card['subtitle']))
    return lines
