"""Source-anchored, restrained kinetic text. Text cards never rewrite narration."""
import math,re
from .media_catalog import map_anchor

# Curated, deterministic render presets. Model output may select an id but may
# not inject ASS tags, colors, coordinates, or arbitrary FFmpeg expressions.
STYLE_PRESETS = {
    # ASS color tags use BBGGRR order; these accents are encoded in that order.
    'clean': {'accent':'D0EA9B','panel':'29211B','title':'FFFFFF','subtitle':'DDDDD8','alpha':'22'},
    'festival': {'accent':'FFC857','panel':'351529','title':'FFFFFF','subtitle':'FFE8CF','alpha':'18'},
    'pop': {'accent':'FFE537','panel':'111827','title':'FFFFFF','subtitle':'E8F7FF','alpha':'16'},
    'minimal': {'accent':'FFFFFF','panel':'202020','title':'FFFFFF','subtitle':'E0E0E0','alpha':'35'},
}

def tc(t):
    n=round(t*100);return f'{n//360000}:{n//6000%60:02}:{n//100%60:02}.{n%100:02}'
def clean(text):return str(text).replace('\\','').replace('{','').replace('}','').replace('\n',r'\N')

def compile_cards(specs,clips):
    cards=[]
    for spec in specs:
        start,end=map_anchor(clips,spec['media_id'],float(spec['source_start']),float(spec['source_end']))
        title=str(spec['title']);subtitle=str(spec.get('subtitle',''))
        if not title or len(title)>18 or len(subtitle)>28:raise ValueError('kinetic text exceeds readable card limits')
        if end-start<1 or end-start>12:raise ValueError('kinetic text needs 1..12 seconds')
        kind=spec.get('kind','keyword')
        if kind not in {'title','step','keyword','summary'}:raise ValueError('unknown card type')
        if not spec.get('reason'):raise ValueError('missing text-to-source rationale')
        cards.append({**spec,'start':round(start,3),'end':round(end,3),'kind':kind})
    cards.sort(key=lambda c:c['start'])
    if any(a['end']>b['start']+.001 for a,b in zip(cards,cards[1:])):raise ValueError('overlapping top-area text cards')
    return cards

def ass_lines(cards,style='clean'):
    if style == 'journal':
        from .journal_text import ass_lines as journal_lines
        return journal_lines(cards)
    if style == 'sticker':
        from .sticker_text import ass_lines as sticker_lines
        return sticker_lines(cards)
    if style not in STYLE_PRESETS:raise ValueError(f'unknown kinetic text style: {style}')
    palette=STYLE_PRESETS[style]
    lines=[]
    def event(c,layer,text):
        y=float(c.get('layout_y',145))
        if not math.isfinite(y) or not 100<=y<=1200:raise ValueError('invalid title placement')
        shift=int(y)-145
        text=re.sub(r'\\pos\((\d+),(\d+)\)',lambda m:rf'\pos({m[1]},{int(m[2])+shift})',text)
        text=re.sub(r'\\move\((\d+),(\d+),(\d+),(\d+),',lambda m:rf'\move({m[1]},{int(m[2])+shift},{m[3]},{int(m[4])+shift},',text)
        lines.append(f'Dialogue: {layer},{tc(c["start"])},{tc(c["end"])},Title,,0,0,0,,{text}')
    for c in cards:
        # Upper safe area, maximum one card. Offset entrance, restrained accent bar.
        event(c,1,rf'{{\an7\pos(60,145)\p1\bord0\shad0\1c&H{palette["panel"]}&\1a&H{palette["alpha"]}&\fad(160,180)}}m 0 18 b 0 8 8 0 18 0 l 922 0 b 932 0 940 8 940 18 l 940 174 b 940 184 932 192 922 192 l 18 192 b 8 192 0 184 0 174{{\p0}}')
        event(c,2,rf'{{\an7\pos(60,163)\p1\bord0\shad0\1c&H{palette["accent"]}&\fad(160,180)}}m 0 0 l 8 0 l 8 156 l 0 156{{\p0}}')
        prefix=clean(c.get('label',''))
        title=clean(c['title'])
        if prefix:title=rf'{{\1c&H{palette["accent"]}&}}'+prefix+rf'  {{\1c&H{palette["title"]}&}}'+title
        size=min(82 if c['kind']=='title' else 70,max(28,int(884/max(1,len(c['title'])+len(prefix)+2))))
        animation=c.get('animation','slide')
        if animation=='slide':motion=r'\move(88,192,88,179,0,220)\fad(140,180)'
        elif animation=='fade':motion=r'\pos(88,179)\fad(180,180)'
        elif animation=='pop':motion=r'\pos(88,179)\fscx72\fscy72\t(0,180,\fscx100\fscy100)\fad(100,180)'
        else:raise ValueError(f'unknown kinetic text animation: {animation}')
        effect=c.get('effect','none')
        if effect=='none':decoration=r'\bord0\shad0'
        elif effect=='outline':decoration=rf'\bord3\3c&H{palette["accent"]}&\shad0'
        elif effect=='glow':decoration=rf'\bord3\3c&H{palette["accent"]}&\blur1.2\shad0'
        elif effect=='shadow':decoration=rf'\bord0\shad3\4c&H101010&'
        else:raise ValueError(f'unknown kinetic text effect: {effect}')
        event(c,3,rf'{{\an7{motion}\fs{size}{decoration}\b1}}'+title)
        if c.get('subtitle'):
            subtitle_size=min(36,max(24,int(876/max(1,len(c['subtitle'])))))
            event(c,3,rf'{{\an7\pos(92,274)\fs{subtitle_size}\1c&H{palette["subtitle"]}&\bord0\shad0\b0\fad(200,180)}}'+clean(c['subtitle']))
    return lines
