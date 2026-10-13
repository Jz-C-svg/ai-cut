"""Vector sticker typography with a short overshoot entrance, rendered by libass."""
import math


def ass_lines(cards):
    from .kinetic_text import clean, tc
    lines = []
    for card in cards:
        start, end = float(card['start']), float(card['end'])
        if not math.isfinite(start + end) or start < 0 or end <= start:
            raise ValueError('invalid sticker interval')
        title = str(card['title'])
        if not title or len(title) > 16:
            raise ValueError('sticker title must contain 1..16 characters')
        warning = card.get('variant') == 'warning'
        fill = '77DCFF' if not warning else 'A8D7FF'
        ink = '33251C'
        motion = r'\fscx65\fscy65\t(0,150,\fscx108\fscy108)\t(150,290,\fscx100\fscy100)\fad(65,140)'
        def event(layer, tags, text):
            lines.append(f'Dialogue: {layer},{tc(start)},{tc(end)},Title,,0,0,0,,{{{tags}}}{text}')
        # A separate angled tag, no full-width title panel.
        event(1, rf'\an7\pos(135,98)\frz4\p1\bord5\3c&HFFFFFF&\shad0\1c&H{ink}&{motion}',
              r'm 0 0 l 280 0 l 266 56 l 0 56{\p0}')
        event(2, rf'\an5\pos(264,120)\frz4\fnMicrosoft YaHei\fs33\b1\bord0\shad0\1c&HFFFFFF&{motion}',
              clean(card.get('label', '清单重点')))
        size = min(126, int(825 / max(len(title), 1)))
        common = rf'\an5\pos(540,218)\frz-2\fnMicrosoft YaHei\fs{size}\b1\shad0{motion}'
        # White die-cut edge, dark inner contour and bright letter faces.
        event(3, common + r'\bord14\3c&HFFFFFF&\1c&HFFFFFF&', clean(title))
        event(4, common + rf'\bord6\3c&H{ink}&\1c&H{fill}&', clean(title))
        # Marker underline and a sparkle are graphic accents, not image assets.
        event(2, rf'\an7\pos(240,296)\p1\bord0\shad0\1c&H{fill}&{motion}',
              r'm 0 0 l 580 -8 l 570 7 l 8 14{\p0}')
        event(3, rf'\an7\pos(914,126)\p1\bord4\3c&HFFFFFF&\shad0\1c&H{fill}&{motion}',
              r'm 24 0 l 30 20 l 50 26 l 30 32 l 24 54 l 18 32 l 0 26 l 18 20{\p0}')
        if card.get('subtitle'):
            event(4, rf'\an5\pos(540,337)\fnMicrosoft YaHei\fs36\b1\bord3\3c&H{ink}&\shad0\1c&HFFFFFF&\fad(200,140)',
                  clean(card['subtitle']))
    return lines
