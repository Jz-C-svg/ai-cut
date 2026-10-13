"""Evidence-bound planning: preserve speech, quote highlights, fail closed."""
import json
import math
from pathlib import Path

ROLES = {'开场介绍', '动作演示', '补充说明', '注意事项', '提问与解释', '结尾说明'}

def check_plan(plan, evidence, uncertain_ids=()):
    errors=[]
    if not isinstance(plan,dict) or set(plan)!={'groups'}:
        return ['schema: expected groups only']
    groups=plan['groups']
    if not isinstance(groups,list) or not groups:
        return ['schema: nonempty groups required']
    lookup={s['id']:s for s in evidence['segments']}
    style_ids={s['id'] for s in evidence['styles']}
    used=[]; flowers=0
    for n,g in enumerate(groups):
        if not isinstance(g,dict) or set(g)!={'segment_ids','role','flower'}:
            errors.append(f'group {n}: schema');continue
        ids=g['segment_ids']
        if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or x not in lookup for x in ids):
            errors.append(f'group {n}: invalid source ids');continue
        used.extend(ids)
        if not isinstance(g['role'],str) or g['role'] not in ROLES: errors.append(f'group {n}: unsupported role')
        f=g['flower']
        if f is None: continue
        flowers+=1
        if not isinstance(f,dict) or set(f)!={'text','segment_id','style_reference_id'}:
            errors.append(f'group {n}: flower schema');continue
        sid=f['segment_id'];txt=f['text']
        if not isinstance(sid,str) or sid not in ids:
            errors.append(f'group {n}: flower evidence outside group');continue
        if not isinstance(txt,str) or not 2<=len(txt)<=12 or txt not in lookup[sid]['text']:
            errors.append(f'group {n}: flower must be short verbatim source substring')
        if sid in uncertain_ids: errors.append(f'group {n}: unresolved ASR used as flower')
        if not isinstance(f['style_reference_id'],str) or f['style_reference_id'] not in style_ids: errors.append(f'group {n}: nonexistent style')
    if used!=[s['id'] for s in evidence['segments']]: errors.append('source coverage/order/duplicates')
    cap=min(2,max(1,math.ceil(len(groups)/2)))
    if flowers>cap: errors.append(f'flower density: {flowers} exceeds {cap}')
    return errors

def materialize(plan,evidence,uncertain_ids):
    errors=check_plan(plan,evidence,uncertain_ids)
    if errors: raise ValueError(errors)
    lookup={s['id']:s for s in evidence['segments']}
    result={'title':'原片讲解整理（待校对）','source':evidence['source'],'source_sha256':evidence['source_sha256'],
            'groups':[],'review_status':'requires_audiovisual_review','release_ready':False,
            'unknowns':{'exact_font':None,'original_animation':None,'original_text_start_end':None}}
    for i,g in enumerate(plan['groups'],1):
        segs=[lookup[x] for x in g['segment_ids']]
        result['groups'].append(dict(g,number=i,source_start=segs[0]['start'],source_end=segs[-1]['end'],
            source_segments=segs,uncertain_segment_ids=[s['id'] for s in segs if s['id'] in uncertain_ids],
            timing_basis='ASR segment boundaries, not original flower timing',design_status='proposed'))
    return result

def write_markdown(result,path):
    lines=['# '+result['title'],'','保留原稿；花字仅摘录原话。所有术语、动作与讲者解释仍须回看原片。','']
    for g in result['groups']:
        lines += [f'## {g["number"]}. {g["role"]}',f'原片 ASR 范围：{g["source_start"]}–{g["source_end"]} 秒。','',
                  '原话：'+''.join(s['text'] for s in g['source_segments']),
                  '花字建议：'+(g['flower']['text'] if g['flower'] else '无'),
                  '待校对片段：'+(', '.join(g['uncertain_segment_ids']) or '所有 ASR 尚未逐句回听'), '']
    Path(path).write_text('\n'.join(lines),encoding='utf-8')
