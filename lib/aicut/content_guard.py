"""Explicit source coverage and traceable, source-specific caption corrections."""
import math
from pathlib import Path

def coverage_issues(clips, required, resolve=lambda p:p):
    issues=[]
    for unit in required:
        try:
            start,end=float(unit['start']),float(unit['end'])
            if not all(math.isfinite(x) for x in (start,end)) or not 0<=start<end:
                raise ValueError('invalid required interval')
            source=Path(resolve(unit['file'])).resolve()
            spans=sorted((max(start,float(c['start'])),min(end,float(c['end'])))
                         for c in clips if Path(resolve(c['file'])).resolve()==source)
            cursor=start
            for a,b in spans:
                if b<=a:continue
                if a>cursor+.001:break
                cursor=max(cursor,b)
            if cursor<end-.001:issues.append(f"必保留内容缺失: {unit.get('label',source.name)}")
        except (KeyError,ValueError,TypeError,AttributeError):
            issues.append('required_source_ranges 格式或时间无效')
    return issues

def correct_captions(segments,rules,source_id):
    """No global homophone guessing; rule author and original text are retained."""
    result=[];changes=[]
    for index,segment in enumerate(segments):
        row=dict(segment);text=str(row['text']).replace('\n','')
        original=text
        for rule in rules:
            if rule.get('source_id')!=source_id:continue
            if not rule.get('basis') or rule.get('provenance') not in {'assistant_context_review_unverified','human_reviewed'}:
                raise ValueError('caption correction lacks provenance')
            old,new=rule['old'],rule['new']
            # Numerical changes always need human review.
            if any(ch.isdigit() for ch in old+new) and rule['provenance']!='human_reviewed':
                raise ValueError('unreviewed numerical correction')
            if old and old in text:
                text=text.replace(old,new)
                changes.append({'segment':index+1,'old':old,'new':new,'basis':rule['basis'],
                                'provenance':rule['provenance'],'source_id':source_id})
        row['text']=text
        if text!=original:row['original_text']=original
        result.append(row)
    return result,changes
