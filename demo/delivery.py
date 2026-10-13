"""Evidence-based technical QA and explicit human acceptance."""
import hashlib, json, math, subprocess
from pathlib import Path
from datetime import datetime, timezone
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def probe(path):
    p=subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],capture_output=True,check=True)
    return json.loads(p.stdout)
def report(job,output):
    meta=probe(output); video=next(s for s in meta['streams'] if s['codec_type']=='video')
    expected=sum(s['end']-s['start'] for s in job['plan']['segments']); actual=float(meta['format']['duration'])
    audio=any(s['codec_type']=='audio' for s in meta['streams'])
    p=subprocess.run(['ffmpeg','-v','error','-i',str(output),'-f','null','-'],capture_output=True)
    checks={'complete_decode':p.returncode==0,'duration_matches_plan':abs(actual-expected)<=.15,'portrait_720p':video['width']==720 and video['height']==1280,'audio_preserved':audio==job['media']['audio'],'nonempty_output':Path(output).stat().st_size>1000}
    return {'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'job_id':job['id'],'scenario':'product_brand_short','plan_version':job.get('plan_version',1),'source_sha256':job.get('source_digest'),'plan_sha256':job['plan_hash'],'output_sha256':sha(output),'mode':job['mode'],'expected_duration':round(expected,3),'actual_duration':actual,'checks':checks,'technical_status':'passed' if all(checks.values()) else 'needs_fix','human_review':{'subtitle_readability':'pending','visual_quality':'pending','audio_sync':'pending','facts_and_rights':'pending'},'acceptance_status':'pending','notes':'Technical checks do not prove aesthetics, factual accuracy or buyer acceptance.'}
def accept(current,body):
    if body.get('output_sha256')!=current['output_sha256'] or body.get('plan_sha256')!=current['plan_sha256']: raise ValueError('验收对象已变更，请刷新报告')
    states=body.get('checks',{})
    if set(states)!=set(current['human_review']) or any(v not in ('passed','needs_fix','pending') for v in states.values()): raise ValueError('请逐项填写验收结果')
    reviewer=str(body.get('reviewer','')).strip()
    if not reviewer or len(reviewer)>80: raise ValueError('请填写验收人（最多80字）')
    result=dict(current);result['human_review']=states;result['reviewer']=reviewer;result['reviewed_at']=datetime.now(timezone.utc).isoformat();result['review_notes']=str(body.get('notes',''))[:2000]
    result['acceptance_status']='accepted' if current['technical_status']=='passed' and all(v=='passed' for v in states.values()) else ('needs_fix' if 'needs_fix' in states.values() or current['technical_status']!='passed' else 'pending')
    return result
