"""Render a validated folder plan with timed captions and auditable term corrections."""
import argparse, hashlib, json, re, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
from aicut.professional_editing import word_captions, polish_captions, caption_warnings, compile_plan, validate_constraints
from aicut.production import project_name
from aicut.media_catalog import choose_broll, map_anchor
from aicut.kinetic_text import compile_cards, ass_lines
from aicut.kinetic_text_agent import generate_cards
import build_draft

def corrected_words(words, corrections, audit):
    chars=[]
    for w in words:
        text=w['word'].strip()
        for i,ch in enumerate(text):
            chars.append({'word':ch,'start':w['start']+(w['end']-w['start'])*i/max(1,len(text)),
                          'end':w['start']+(w['end']-w['start'])*(i+1)/max(1,len(text))})
    for item in corrections:
        old,new=item['old'],item['new']
        text=''.join(c['word'] for c in chars)
        positions=[m.start() for m in re.finditer(re.escape(old),text)]
        for pos in reversed(positions):
            start,end=chars[pos]['start'],chars[pos+len(old)-1]['end']
            chars[pos:pos+len(old)]=[{'word':ch,'start':start+(end-start)*i/len(new),
                                    'end':start+(end-start)*(i+1)/len(new)} for i,ch in enumerate(new)]
            audit.append({**item,'source_start':start,'source_end':end})
    # Keep terminology and short grammatical units together across captions.
    terms=sorted({'腹直肌','腹横肌','腹肌','腰方肌','脐上','脐中','脐下','屈膝','两边','个人情况','千篇一律','并不是','微微抬头','三个位置','这个地方','弯腰活动','情况下','两指','三指'},key=len,reverse=True)
    grouped=[];i=0;text=''.join(c['word'] for c in chars)
    while i<len(chars):
        token=next((t for t in terms if text.startswith(t,i)),chars[i]['word'])
        grouped.append({'word':token,'start':chars[i]['start'],'end':chars[i+len(token)-1]['end']})
        i+=len(token)
    return grouped

def timecode(t):
    cs=round(max(0,t)*100);return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'
def ass_escape(text):
    return str(text).replace('\\','').replace('{','').replace('}','').replace('\n',r'\N')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--plan',required=True)
    ap.add_argument('--name',required=True);ap.add_argument('--out',required=True)
    ap.add_argument('--presentation',required=True,help='JSON title, cta, footer and optional term corrections')
    ap.add_argument('--build-draft',action='store_true');args=ap.parse_args()
    project_name(args.name);base=Path(args.plan)
    plan=json.loads((base/'validated-plan.json').read_text(encoding='utf-8'))
    media=json.loads((base/'media.json').read_text(encoding='utf-8'))
    evidence=json.loads((base/'learning-evidence.json').read_text(encoding='utf-8'))
    presentation=json.loads(Path(args.presentation).read_text(encoding='utf-8'))
    reviewpath=base/'source-review.json'
    if reviewpath.exists():validate_constraints({'selections':plan['decisions']},json.loads(reviewpath.read_text(encoding='utf-8')))
    # Revalidate the saved plan, instead of trusting editable time ranges.
    compiled=compile_plan({'selections':plan['decisions']},media,[x['id'] for x in evidence['evidence']],padding=plan['policy']['padding_seconds'],max_duration=plan.get('duration_limit',180))
    if compiled['clips']!=plan['clips']:raise ValueError('saved plan changed after validation')
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    if out.resolve() in {Path(m['file']).resolve() for m in media}:raise ValueError('output aliases a source')
    lookup={m['id']:m for m in media};captions=[];audit=[];offset=0;chapters=[]
    for clip in plan['clips']:
        m=lookup[clip['media_id']]
        segs=m['segments'][clip['first_segment']-1:clip['last_segment']]
        words=[]
        for s in segs:
            timed=[dict(w) for w in s.get('words',[])]
            if timed and not re.search(r'[。！？!?，,；;]$',timed[-1]['word']):
                timed[-1]['word']+='。'
            words.extend(timed)
        if not words:raise ValueError('word timestamps required for captions')
        rules=[r for r in presentation.get('corrections',[]) if r['media_id']==m['id']]
        corrected=corrected_words(words,rules,audit)
        for c in polish_captions(word_captions(corrected,max_span=4.5)):
            c['start']=max(0,c['start']-clip['start'])+offset
            c['end']=min(clip['end'],c['end'])-clip['start']+offset
            c['text']=c['text'].replace(',','，').strip(' ，。')
            if c['end']>c['start'] and c['text']:captions.append(c)
        length=clip['end']-clip['start']
        chapters.append({'start':offset,'end':offset+length,'media_id':m['id']})
        offset+=length
    def save(name,obj):
        p=base/name;p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');return p
    subpath=save('final-captions.json',{'segments':captions})
    save('subtitle-corrections.json',{'method':'Explicit domain spelling corrections; original audio unchanged; intra-word character timing interpolated within original word bounds.', 'changes':audit})
    warnings=caption_warnings(captions)
    save('caption-review.json',{'warnings':warnings,'caption_count':len(captions)})
    ass=['[Script Info]','ScriptType: v4.00+','PlayResX: 1080','PlayResY: 1920','ScaledBorderAndShadow: yes','',
         '[V4+ Styles]', 'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
         'Style: Caption,Microsoft YaHei,76,&H00FFFFFF,&H00FFFFFF,&H00241C19,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,72,72,300,1',
         'Style: Title,Microsoft YaHei,94,&H00FFFFFF,&H00FFFFFF,&H00453E12,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,7,60,60,70,1',
         'Style: Tag,Microsoft YaHei,48,&H00D4EABE,&H00FFFFFF,&H00302820,&H90000000,0,0,0,0,100,100,0,0,1,2,1,7,60,60,70,1',
         'Style: Footer,Microsoft YaHei,36,&H00FFFFFF,&H00FFFFFF,&H00302820,&H90000000,0,0,0,0,100,100,0,0,1,2,1,2,60,60,215,1',
         '', '[Events]','Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    def event(start,end,style,text,override=''):
        ass.append(f'Dialogue: 0,{timecode(start)},{timecode(end)},{style},,0,0,0,,{override}{ass_escape(text)}')
    for c in captions:event(c['start'],c['end'],'Caption',c['text'])
    generated_cards=None
    card_specs=presentation.get('kinetic_cards',[])
    if presentation.get('generate_kinetic_cards') and not card_specs:
        generated_cards=generate_cards(plan['clips'],media,goal=presentation.get('goal',''))
        cards=generated_cards['cards']
    else:
        cards=compile_cards(card_specs,plan['clips'])
    card_style=(generated_cards or {}).get('style',presentation.get('kinetic_text_style','clean'))
    if cards:
        ass.extend(ass_lines(cards,style=card_style))
    if cards or generated_cards:
        save('kinetic-text-plan.json',{'cards':cards,'generated':generated_cards,
             'style':card_style,
             'max_simultaneous_cards':1,'subtitle_zone':'bottom','card_zone':'top'})
    elif not generated_cards:
        event(0,5,'Tag',presentation.get('eyebrow','现场讲解'),r'{\pos(65,165)\fad(160,180)}')
        event(.15,5,'Title',presentation['title'],r'{\pos(60,225)\fad(160,180)}')
        for chapter in chapters[1:]:
            label=presentation.get('chapters',{}).get(chapter['media_id'])
            if label:event(chapter['start'],min(chapter['end'],chapter['start']+4.5),'Tag',label,r'{\pos(65,170)\fad(120,180)}')
    if presentation.get('footer'):event(0,offset,'Footer',presentation['footer'])
    if not cards and not generated_cards:event(max(0,offset-5),offset,'Tag',presentation['cta'],r'{\pos(65,170)\fad(120,0)}')
    for notice in presentation.get('review_notices',[]):
        ns,ne=map_anchor(plan['clips'],notice['media_id'],notice['source_start'],notice['source_end'])
        event(ns,ne,'Footer',notice['text'],r'{\an2\pos(540,1790)\fs30\1c&HADDAFF&}')
    broll=[]
    if presentation.get('broll_requests'):
        catalog=json.loads(Path(presentation['asset_catalog']).read_text(encoding='utf-8'))
        used=set()
        for request in presentation['broll_requests']:
            at,end=map_anchor(plan['clips'],request['media_id'],request['source_start'],request['source_end'])
            item=choose_broll(catalog,{**request,'at':at,'duration':end-at},offset,used)
            broll.append(item);used.add(item['asset_id'])
            if request.get('label'):event(at,end,'Footer',request['label'],r'{\an7\pos(70,1460)\fs32}')
        save('asset-retrieval-evidence.json',{'selected':broll,'indexed_assets':len(catalog['assets'])})
    asspath=base/'final.ass';asspath.write_text('\n'.join(ass)+'\n',encoding='utf-8-sig')
    clips=[{**c,'滤镜':'清晰明亮','滤镜强度':15} for c in plan['clips']]
    sheet={'project':args.name,'width':1080,'height':1920,'fps':30,'clips':clips,
           'duration_limit':plan.get('duration_limit',180),'broll':broll,'kinetic_text':cards,
           'kinetic_text_style':card_style,
           'subtitles_json':str(subpath),'subtitle_ass':str(asspath),'cover_text':presentation['title'],
           'cta':presentation['cta'],'timeline_prepared':True,'style':'口播标准',
           'audio_normalization':{'integrated_lufs':-16,'true_peak_db':-1.5},
           'learning_context':{'signature':evidence['signature'],'applied_evidence_ids':sorted({x for d in plan['decisions'] for x in d['evidence_ids']})}}
    problems=build_draft.quality_gates(sheet)
    if problems:raise ValueError('; '.join(problems))
    sheetpath=save('final.sheet.json',sheet)
    source_hashes={}
    for src in {c['file'] for c in clips+broll}:
        with open(src,'rb') as f:source_hashes[src]=hashlib.file_digest(f,'sha256').hexdigest()
    save('source-hashes.json',source_hashes)
    if args.build_draft:
        subprocess.run([sys.executable,str(ROOT/'scripts/build_draft.py'),str(sheetpath)],check=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/export_video.py'),'--sheet',str(sheetpath),'--out',str(out)],check=True)
    for src,expected in source_hashes.items():
        with open(src,'rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest()!=expected:raise ValueError('source changed during rendering')
    save('render-result.json',{'output':str(out),'duration':offset,'captions':len(captions),'corrections':len(audit),'broll_count':len(broll),'kinetic_card_count':len(cards),
                               'source_hashes_unchanged':True,'quality_gates':[], 'caption_warnings':warnings})
    print(json.dumps({'output':str(out),'duration':offset,'captions':len(captions),'caption_warnings':warnings},ensure_ascii=False))
if __name__=='__main__':main()
