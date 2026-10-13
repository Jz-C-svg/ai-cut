"""Local media discovery with explicit relevance and reviewed B-roll ranges."""
import hashlib,json,math,re,subprocess
from pathlib import Path

TYPES={'.mov':'video','.mp4':'video','.mkv':'video','.avi':'video','.m4v':'video','.webm':'video',
       '.jpg':'image','.jpeg':'image','.png':'image','.webp':'image','.wav':'audio','.mp3':'audio','.m4a':'audio'}
def terms(text):
    result=set(re.findall(r'[a-z0-9_-]+',text.lower()))
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        result.update(run[i:i+2] for i in range(len(run)-1))
    return result

def index_library(root, previous=None, annotations=None):
    root=Path(root).resolve();previous=previous or {};annotations=annotations or {}
    cache={r['file']:r for r in previous.get('assets',[])};assets=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in TYPES:continue
        if not path.resolve().is_relative_to(root):continue
        st=path.stat();fingerprint={'size':st.st_size,'mtime_ns':st.st_mtime_ns}
        old=cache.get(str(path),{})
        fresh=old.get('fingerprint')==fingerprint
        row={'id':hashlib.sha256(str(path).casefold().encode()).hexdigest()[:16],
             'file':str(path),'relative':path.relative_to(root).as_posix(),'group':str(path.parent),
             'type':TYPES[path.suffix.lower()],'fingerprint':fingerprint,
             'metadata_basis':'path_and_file_stat','reviewed':False}
        if fresh:
            for key in ('probe','probe_fingerprint','description','tags','usable_ranges','reviewed','review_note','transcript_text','transcript_basis'):
                if key in old:row[key]=old[key]
        ann=annotations.get(str(path),{})
        # Reviewed visual descriptions belong to this exact media revision.
        if ann and ann.get('fingerprint')==fingerprint:
            row.update({k:ann[k] for k in ('description','tags','usable_ranges','reviewed','review_note','transcript_text','transcript_basis') if k in ann})
        row['search_text']=' '.join([row['relative'],row.get('description',''),' '.join(row.get('tags',[])),row.get('transcript_text','')])
        assets.append(row)
    return {'version':'media-catalog-2','root':str(root),'assets':assets,
            'limitations':'Filename matches are candidates, not scene understanding; only reviewed ranges may auto-fill B-roll.'}

def search(catalog,query,*,same_group=None,kind=None,reviewed_only=False,limit=8):
    query_terms=terms(query);ranked=[]
    for row in catalog['assets']:
        if kind and row['type']!=kind or reviewed_only and not row.get('reviewed'):continue
        shared=query_terms & terms(row['search_text'])
        if not shared:continue
        score=2*len(shared)+4*int(row.get('reviewed',False))+3*int(row['group']==same_group)
        ranked.append({**row,'score':score,'matched_terms':sorted(shared),
                       'selection_status':'reviewed_candidate' if row.get('reviewed') else 'needs_scene_review'})
    return sorted(ranked,key=lambda r:(-r['score'],r['file']))[:limit]

def probe_asset(row):
    path=Path(row['file']);st=path.stat()
    if {'size':st.st_size,'mtime_ns':st.st_mtime_ns}!=row['fingerprint']:
        raise ValueError('asset changed since indexing')
    if row.get('probe_fingerprint')==row['fingerprint']:return row['probe']
    raw=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type,width,height:stream_side_data=rotation','-of','json',str(path)],capture_output=True,text=True,check=True,timeout=60)
    result=json.loads(raw.stdout);row['probe']=result;row['probe_fingerprint']=dict(row['fingerprint']);return result

def choose_broll(catalog,request,total,used=()):
    candidates=search(catalog,request['query'],same_group=request.get('same_group'),kind='video',reviewed_only=True,limit=20)
    for candidate in candidates:
        if candidate['id'] in used:continue
        metadata=probe_asset(candidate);duration=float(metadata['format']['duration'])
        ranges=[r for r in candidate.get('usable_ranges',[]) if request.get('role') in r.get('roles',[])]
        for span in ranges:
            start,end=float(span['start']),float(span['end']);length=float(request['duration']);at=float(request['at'])
            if not all(math.isfinite(x) for x in (start,end,length,at)):raise ValueError('nonfinite B-roll')
            if not 0<=start<end<=duration or not 0<=at<at+length<=total or length<=0:continue
            if end-start+.001<length:continue  # No implicit freeze or slow motion.
            return {'asset_id':candidate['id'],'file':candidate['file'],'start':start,'end':start+length,
                    'at':at,'duration':length,'reason':request['reason'],'query':request['query'],
                    'review_note':candidate.get('review_note'),'fingerprint':candidate['fingerprint']}
    raise ValueError('No reviewed, sufficiently long, relevant B-roll: '+request['query'])

def map_anchor(clips,media_id,start,end):
    offset=0
    for clip in clips:
        if clip['media_id']==media_id and clip['start']-.001<=start<end<=clip['end']+.001:
            speed=float(clip.get('speed',1))
            return offset+(start-clip['start'])/speed,offset+(end-clip['start'])/speed
        offset+=(clip['end']-clip['start'])/float(clip.get('speed',1))
    raise ValueError('Annotation anchor is outside retained source content')
