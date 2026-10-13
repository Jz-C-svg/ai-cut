"""Traceable editing helpers. Dataset calibration is not ASR/model training."""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if not all(math.isfinite(v) for v in (start, end)) or end <= start:
            raise ValueError('invalid interval')
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def speech_windows(intervals, duration, padding=.12, protected=()):
    """Protect annotated speech plus optional silent but essential demonstrations."""
    if not math.isfinite(duration) or duration <= 0 or not 0 <= padding <= 1:
        raise ValueError('invalid duration/padding')
    return merge_intervals([(max(0, s-padding), min(duration, e+padding))
                            for s, e in intervals if 0 <= s < e <= duration]
                           + [(max(0, s), min(duration, e)) for s, e in protected
                              if 0 <= s < e <= duration])


def read_records(raw, source, seen, warnings):
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            required = ('id', 'title', 'text', 'source_url', 'license', 'content_sha256',
                        'source_sha256', 'limitations', 'dataset_version')
            if row.get('tier') != 'professional_reference' or row.get('split') != 'train':
                raise ValueError('non-training record')
            if any(not isinstance(row.get(k), str) or not row[k].strip() for k in required):
                raise ValueError('missing provenance')
            if row['id'] in seen or hashlib.sha256(row['text'].encode()).hexdigest() != row['content_sha256']:
                raise ValueError('duplicate/hash mismatch')
            seen.add(row['id'])
            yield {**row, 'category': 'professional_dataset', 'source': source,
                   'locator': f'line:{number}'}
        except (ValueError, TypeError, KeyError, AttributeError):
            warnings.append(f'{source}:{number}: invalid professional record')


def compile_plan(plan, media, evidence_ids, padding=.12, max_duration=180):
    """LLM can select whole transcript segments, never invent files/timestamps."""
    lookup = {m['id']: m for m in media}
    clips, used, decisions = [], set(), []
    if not isinstance(plan.get('selections'), list) or not plan['selections']:
        raise ValueError('empty selections')
    for selection in plan['selections']:
        mid = selection['media_id']
        if mid not in lookup:
            raise ValueError('unknown media')
        m = lookup[mid]
        a, b = selection['first_segment'], selection['last_segment']
        if type(a) is not int or type(b) is not int or not 1 <= a <= b <= len(m['segments']):
            raise ValueError('invalid segment range')
        refs = selection.get('evidence_ids', [])
        if not refs or not set(refs) <= set(evidence_ids) or not selection.get('reason'):
            raise ValueError('missing/unknown evidence')
        keys = {(mid, i) for i in range(a, b+1)}
        if used & keys:
            raise ValueError('duplicate transcript segment')
        used.update(keys)
        selected = m['segments'][a-1:b]
        start, end = float(selected[0]['start']), float(selected[-1]['end'])
        duration = float(m['duration'])
        if not all(math.isfinite(x) for x in [start, end, duration]) or not 0 <= start < end <= duration+.05:
            raise ValueError('out-of-source selection')
        # Respect an adjacent sentence even when adding the calibrated handle.
        prev_end = float(m['segments'][a-2]['end']) if a > 1 else 0
        next_start = float(m['segments'][b]['start']) if b < len(m['segments']) else duration
        start = max(prev_end, start-padding, 0)
        end = min(next_start, end+padding, duration)
        clips.append({'file': m['file'], 'start': round(start, 3), 'end': round(end, 3), 'speed': 1.0,
                      'media_id': mid, 'first_segment': a, 'last_segment': b})
        decisions.append({**selection, 'source_text': ''.join(s['text'] for s in selected)})
    total = sum(c['end']-c['start'] for c in clips)
    if total > max_duration:
        raise ValueError(f'plan exceeds duration limit: {total:.2f}')
    # Adjacent source intervals cannot overlap due to a padding handle.
    for mid in {c['media_id'] for c in clips}:
        ordered = sorted([c for c in clips if c['media_id']==mid], key=lambda c:c['start'])
        if any(a['end'] > b['start']+.001 for a,b in zip(ordered,ordered[1:])):
            raise ValueError('overlapping source intervals')
    return {'clips': clips, 'duration': round(total, 3), 'decisions': decisions}


def validate_constraints(plan, review):
    """Enforce review exclusions and necessary demonstration prerequisites."""
    selections=plan.get('selections',[])
    for pick in selections:
        for banned in review.get('excluded_ranges',[]):
            if (pick['media_id']==banned['media_id'] and pick['first_segment']<=banned['last_segment']
                    and pick['last_segment']>=banned['first_segment']):
                raise ValueError('model selected an excluded source range')
    for required in review.get('required_ranges',[]):
        covered={i for s in selections if s['media_id']==required['media_id']
                 for i in range(s['first_segment'],s['last_segment']+1)}
        if not set(range(required['first_segment'],required['last_segment']+1))<=covered:
            raise ValueError('missing required source context: '+required.get('reason',''))
    coverage=content_coverage(plan,review.get('content_units',[]))
    missing=[x['title'] for x in coverage if x['required'] and x['coverage']<1]
    if missing:raise ValueError('missing content units: '+', '.join(missing))

def content_coverage(plan,units):
    kept={(s['media_id'],i) for s in plan.get('selections',[]) for i in range(s['first_segment'],s['last_segment']+1)}
    result=[]
    for unit in units:
        expected={(r['media_id'],i) for r in unit['ranges'] for i in range(r['first_segment'],r['last_segment']+1)}
        if not expected:raise ValueError('empty content unit')
        result.append({'id':unit['id'],'title':unit['title'],'required':unit.get('required',True),
                       'coverage':len(expected&kept)/len(expected),'missing':[{'media_id':m,'segment':i} for m,i in sorted(expected-kept)]})
    return result


def word_captions(words, max_chars=15, max_span=3.5):
    """Keep genuine word timing. Do not manufacture uniform sentence timing."""
    out, group = [], []
    for word in words:
        s, e = float(word['start']), float(word['end'])
        text = str(word.get('word', '')).strip()
        if not text:
            continue
        if not math.isfinite(s+e) or s < 0 or e < s:
            raise ValueError('invalid word timestamp')
        if group and (len(''.join(w['word'] for w in group))+len(text) > max_chars
                      or e-group[0]['start'] > max_span or s-group[-1]['end'] > .65):
            out.append({'start':group[0]['start'], 'end':group[-1]['end'],
                        'text':''.join(w['word'] for w in group)})
            group=[]
        group.append({'start':s,'end':e,'word':text})
        if re.search(r'[。！？!?，,；;]$',text):
            out.append({'start':group[0]['start'],'end':group[-1]['end'],
                        'text':''.join(w['word'] for w in group)})
            group=[]
    if group:
        out.append({'start':group[0]['start'],'end':group[-1]['end'],
                    'text':''.join(w['word'] for w in group)})
    for i, item in enumerate(out):
        next_start=out[i+1]['start'] if i+1<len(out) else item['end']+.18
        item['end']=min(next_start,max(item['end']+.12,item['start']+.5))
    return out


def caption_warnings(captions, max_cps=9):
    warnings=[]
    for i,c in enumerate(captions,1):
        count=len(re.sub(r'[\s，。！？、,!?;；：:]','',c['text']))
        span=c['end']-c['start']
        if span <= 0 or count/max(span,.001)>max_cps:
            warnings.append({'caption':i,'issue':'reading_speed','cps':round(count/max(span,.001),2)})
    return warnings


def polish_captions(captions):
    """Merge tiny trailing fragments; preserve every character and time coverage."""
    out=[]
    for item in captions:
        item=dict(item)
        text=re.sub(r'[\s，。！？、,!?;；：:]','',item['text'])
        if out and len(text)<4 and item['start']-out[-1]['end']<=.4:
            previous=out[-1];combined=previous['text'].replace('\n','')+item['text']
            if len(combined)<=22 and item['end']-previous['start']<=5.5:
                if len(combined)>15:
                    cut=len(combined)//2
                    protected=('腹直肌','腹横肌','腰方肌','并不是','千篇一律','情况下','屈膝','脐上','脐中','脐下')
                    for term in protected:
                        pos=combined.find(term)
                        if pos<cut<pos+len(term):cut=pos
                    combined=combined[:cut]+'\n'+combined[cut:]
                previous['text']=combined;previous['end']=item['end'];continue
        out.append(item)
    return out
