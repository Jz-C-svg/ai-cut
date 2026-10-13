"""Conservative source screening; detected defects remain traceable candidates."""
import math,re,subprocess,copy
from .retake import norm,detect_retakes

COUNT=re.compile(r'^(?:123|321|一二三|三二一)(?:开始|开拍|走|action)?$',re.I)
COMMAND=re.compile(r'^(?:123|321|一二三|三二一)(?:开始|开拍|走|action)$',re.I)
DIRECTING=re.compile(r'重来|再来一遍|重新录|停止录制|你开始吧我来说|别录了')
SENSITIVE=re.compile(r'\d|[一二三四五六七八九十百千万]+(?:克|件|条|毫升|度|码|个)|不|别|只有|如果|必须|注意|强调|记住|总结')

def validate_segments(segments,duration):
    previous=-1
    for s in segments:
        start,end=float(s['start']),float(s['end'])
        if not all(math.isfinite(v) for v in (start,end)) or not 0<=start<end<=duration+.25 or start<previous:
            raise ValueError('invalid/unordered source transcript')
        previous=start

def screen_transcript(segments,duration,protected=()):
    validate_segments(segments,duration)
    findings=[]
    def add(start,end,kind,reason,automatic=False,**extra):
        end=min(duration,end)
        blocked=any(start<b and a<end for a,b in protected)
        findings.append({'start':round(start,3),'end':round(end,3),'kind':kind,'reason':reason,
                         'action':'auto_remove' if automatic and not blocked else 'review',
                         'protected':blocked,'provenance':'automatic_screening_unreviewed',**extra})
    normalized=[norm(s.get('text','')) for s in segments]
    for i,s in enumerate(segments):
        text=normalized[i];start,end=float(s['start']),float(s['end'])
        if COMMAND.fullmatch(text):add(start,end,'production_countdown','isolated countdown plus filming cue',True,text=s['text'])
        elif COUNT.fullmatch(text):
            next_cue=i+1<len(segments) and normalized[i+1] in {'开始','开拍','action'} and segments[i+1]['start']-end<1
            add(start,float(segments[i+1]['end']) if next_cue else end,'production_countdown','countdown; isolated counts need context',next_cue,text=s['text'])
        elif text in {'开始','开拍','action','重来','再来一遍'}:
            add(start,end,'production_cue','short production cue near source edge',start<=5 or duration-end<=3,text=s['text'])
        elif DIRECTING.search(text):add(start,end,'off_topic_direction','filming conversation; check context before removing',
                                       text in {'你开始吧我来说','停止录制','别录了','重新录'},text=s['text'])
    for hit in detect_retakes(segments,sim=.92,gap=2,lookahead=1,edge=0):
        old,new=norm(hit['removed_text']),norm(hit['kept_text'])
        # Adjacent exact repeated statements are conservative; approximate retakes need review.
        exact=old==new and len(old)>=8 and not SENSITIVE.search(old) and hit['keep_start']>=hit['remove_end']
        add(hit['remove_start'],hit['remove_end'],'speech_repeat',hit['reason'],exact,
            removed_text=hit['removed_text'],kept_text=hit['kept_text'],keep_start=hit['keep_start'],keep_end=hit['keep_end'])
    # ASR uncertainty is a review trigger, never evidence that the spoken content
    # should be deleted. Preserve the original transcript/model provenance.
    for seg in segments:
        if not seg.get('低置信度') and 'avg_logprob' not in seg and 'no_speech_prob' not in seg:
            continue
        avg_logprob = float(seg.get('avg_logprob', 0) or 0)
        no_speech_prob = float(seg.get('no_speech_prob', 0) or 0)
        compression_ratio = float(seg.get('compression_ratio', 0) or 0)
        if seg.get('低置信度') or avg_logprob < -0.8 or no_speech_prob > .35 or compression_ratio > 2.4:
            add(float(seg['start']), float(seg['end']), 'asr_uncertain',
                '转写低置信/静音概率高/疑似重复；复核原音与画面，禁止据此自动删词',
                text=seg.get('text', ''), avg_logprob=avg_logprob,
                no_speech_prob=no_speech_prob, compression_ratio=compression_ratio,
                asr_provenance='automatic_asr_unreviewed')
    # Split ASR countdown: never infer timing by evenly dividing sentence characters.
    for i in range(max(0,len(segments)-2)):
        tokens=normalized[i:i+3]
        if tokens not in [['1','2','3'],['3','2','1'],['一','二','三'],['三','二','一']]:continue
        if any(segments[j+1]['start']-segments[j]['end']>1 for j in (i,i+1)):continue
        cue=i+3<len(segments) and normalized[i+3] in {'开始','开拍','action'} and segments[i+3]['start']-segments[i+2]['end']<1
        add(float(segments[i]['start']),float(segments[i+3 if cue else i+2]['end']),'production_countdown','split ASR countdown',cue)
    return findings

def parse_signals(stderr,duration):
    findings=[]
    for kind,start_key,end_key in [('freeze','freeze_start','freeze_end'),('silence','silence_start','silence_end')]:
        pending=None
        for match in re.finditer(rf'\b({start_key}|{end_key})\s*:\s*(-?\d+(?:\.\d+)?)',stderr):
            value=max(0,min(duration,float(match[2])))
            if match[1]==start_key:pending=value
            elif pending is not None:
                if value>pending:findings.append({'start':pending,'end':value,'kind':kind,'action':'review'})
                pending=None
        if pending is not None and pending<duration:findings.append({'start':pending,'end':duration,'kind':kind,'action':'review'})
    for match in re.finditer(r'black_start:(\d+(?:\.\d+)?)\s+black_end:(\d+(?:\.\d+)?)',stderr):
        a,b=float(match[1]),float(match[2])
        if 0<=a<b<=duration+.05:findings.append({'start':a,'end':min(b,duration),'kind':'black','action':'review'})
    return sorted(findings,key=lambda r:(r['start'],r['kind']))

def scan_media(file,duration,has_audio=True):
    cmd=['ffmpeg','-hide_banner','-nostats','-threads','2','-i',str(file),'-vf',
         'scale=360:-2,fps=12,freezedetect=n=-55dB:d=0.7,blackdetect=d=0.25:pix_th=0.08']
    if has_audio:cmd+=['-af','silencedetect=noise=-38dB:d=0.5']
    cmd+=['-f','null','-']
    result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=max(90,int(duration*3)))
    if result.returncode:raise RuntimeError(result.stderr[-1000:])
    return parse_signals(result.stderr,duration)

def classify_visual(findings,segments):
    for item in findings:
        if item['kind']=='silence':continue
        item['has_asr_speech']=any(s['start']<item['end'] and item['start']<s['end'] for s in segments)
        item['reason']='sampled freeze/black candidate; static demonstration and intentional black require review'
        item['provenance']='automatic_media_signal_unreviewed'
    return findings

def compare_takes(records):
    """Cross-file overlap is never permission to discard an entire source."""
    sets={r['source']:{norm(s['text']) for s in r['segments'] if len(norm(s['text']))>=8} for r in records}
    result=[]
    for i,a in enumerate(records):
        for b in records[i+1:]:
            shared=sets[a['source']] & sets[b['source']]
            if len(shared)<2:continue
            result.append({'a':a['source'],'b':b['source'],'shared_sentences':len(shared),
                           'unique_a':len(sets[a['source']]-shared),'unique_b':len(sets[b['source']]-shared),
                           'action':'review_take_overlap','reason':'keep unique additions; shared phrases do not prove equivalent takes'})
    return result

def planning_constraints(media,review):
    """Use the same source screening in multi-file selection and single-video editing."""
    result=copy.deepcopy(review);result.setdefault('excluded_ranges',[]);audit=[]
    required=list(result.get('required_ranges',[]))
    for unit in result.get('content_units',[]):
        if unit.get('required',True):required.extend(unit.get('ranges',[]))
    for source in media:
        protected=[]
        for span in required:
            if span['media_id']==source['id']:
                selected=source['segments'][span['first_segment']-1:span['last_segment']]
                if selected:protected.append((selected[0]['start'],selected[-1]['end']))
        hits=screen_transcript(source['segments'],source['duration'],protected)
        for hit in hits:
            audit.append({'media_id':source['id'],**hit})
            if hit['action']!='auto_remove':continue
            indices=[i for i,s in enumerate(source['segments'],1)
                     if s['start']>=hit['start']-.001 and min(s['end'],source['duration'])<=hit['end']+.001]
            if indices:result['excluded_ranges'].append({'media_id':source['id'],'first_segment':min(indices),
                'last_segment':max(indices),'reason':hit['reason'],'basis':hit['kind'],
                'provenance':'automatic_screening_unreviewed'})
    result['screening_findings']=audit
    return result
