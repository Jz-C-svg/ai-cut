"""Turn sampled OCR into traceable text-overlay candidate events, without invented timing."""
import argparse,hashlib,json,re
from pathlib import Path
from collect_local_dataset import save

def normalized(text):return re.sub(r'\W+','',text).casefold()

def close_enough(a,b):
    return max(abs(x-y) for x,y in zip(a['bbox_normalized'],b['bbox_normalized']))<=.05

def events_for_video(source,frames,duration):
    events=[];active=[]
    for i,frame in enumerate(frames):
        current=[];used=set()
        for block_index,block in enumerate(frame['text_blocks']):
            key=normalized(block['text'])
            if not key:continue
            match=next((j for j,e in enumerate(active) if j not in used and e['_key']==key and close_enough(e['observations'][-1],block)),None)
            if match is None:
                event={'source':source['source'],'source_sha256':source['source_sha256'],'text_ocr':block['text'],'_key':key,'previous_sample_time':frames[i-1]['time'] if i else None,'observations':[]}
            else:used.add(match);event=active[match]
            event['observations'].append({'time':frame['time'],'frame':frame['frame'],'frame_sha256':frame['frame_sha256'],'ocr_block_index':block_index,'text':block['text'],'confidence':block['confidence'],'polygon':block['polygon'],'bbox_normalized':block['bbox_normalized'],'relative_height':block['relative_height'],'vertical_zone':block['vertical_zone'],'large_text_candidate':block['large_text_candidate'],'region_palette_includes_background':block['region_palette_includes_background']})
            current.append(event)
        for j,event in enumerate(active):
            if j not in used:event['next_sample_time']=frame['time'];events.append(event)
        active=current
    for event in active:event['next_sample_time']=None;events.append(event)
    for event in events:
        event.pop('_key');obs=event['observations'];first,last=obs[0]['time'],obs[-1]['time']
        event['id']='text-'+hashlib.sha256(json.dumps([event['source_sha256'],first,obs[0]['ocr_block_index'],event['text_ocr']],ensure_ascii=False).encode()).hexdigest()[:20]
        event['first_observed_at']=first;event['last_observed_at']=last
        event['timing']={'exact_start':None,'exact_end':None,'observed_span_seconds':last-first,'continuous_presence_confirmed':False,'previous_sample_time':event.pop('previous_sample_time'),'next_sample_time':event.pop('next_sample_time'),'source_duration':duration,'note':'Adjacent samples bracket observations only; OCR misses and disappear/reappear events prevent exact onset or offset inference.'}
        event['classification']={'decorative_text_candidate':any(o['large_text_candidate'] for o in obs),'basis':'at_least_one_text_box_height_ge_4_5_percent_of_frame','confirmed_decorative_text':False,'overlay_vs_physical_sign':'unreviewed','multi_line_group_id':None}
        event['style']={'font_name':None,'font_file':None,'exact_fill_color':None,'outline':None,'shadow':None,'background_panel':None,'animation_name':None,'animation_parameters':None,'measured_layout_samples':True,'region_palette_is_font_color':False}
        event['motion']={'status':'unknown_from_sparse_samples','sampled_box_changed':any(o['bbox_normalized']!=obs[0]['bbox_normalized'] for o in obs[1:]),'change_may_be_ocr_jitter':True}
        event['review']={'status':'automatic_unreviewed','needs_text_correction':any(o['confidence']<.8 for o in obs),'needs_style_review':True,'eligible_as_gold_training_label':False}
    return events

def validate(events):
    ids=set()
    for e in events:
        assert e['id'] not in ids;ids.add(e['id'])
        assert e['observations'] and 0<=e['first_observed_at']<=e['last_observed_at']<=e['timing']['source_duration']
        assert e['timing']['exact_start'] is None and e['timing']['exact_end'] is None
        assert e['review']['eligible_as_gold_training_label'] is False
        assert all(0<=o['confidence']<=1 and len(o['bbox_normalized'])==4 for o in e['observations'])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--collection',required=True);a=ap.parse_args();root=Path(a.collection);out=root/'kinetic-text';out.mkdir(exist_ok=True)
    events=[];videos=0;observations=0
    for item in json.loads((root/'index.json').read_text(encoding='utf-8')):
        p=Path(item['production']);production=json.loads(p.read_text(encoding='utf-8'));ocr=json.loads((p.parent/'visual-text.json').read_text(encoding='utf-8'))
        assert production['source_sha256']==ocr['source_sha256'];videos+=1
        rows=events_for_video(ocr,ocr['frames'],production['duration']);events.extend(rows);observations+=sum(len(r['observations']) for r in rows)
    validate(events)
    candidates=[e for e in events if e['classification']['decorative_text_candidate']]
    for name,rows in [('screen-text-events.jsonl',events),('kinetic-text-candidates.jsonl',candidates)]:
        (out/name).write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in rows),encoding='utf-8')
    save(out/'review-queue.json',[{'id':e['id'],'source':e['source'],'text':e['text_ocr'],'first_observed_at':e['first_observed_at'],'frame':e['observations'][0]['frame'],'needs_text_correction':e['review']['needs_text_correction']} for e in candidates])
    summary={'videos_processed':videos,'screen_text_events':len(events),'ocr_observations_preserved':observations,'decorative_text_candidate_events':len(candidates),'videos_with_candidates':len({e['source_sha256'] for e in candidates}),'candidate_events_needing_text_review':sum(e['review']['needs_text_correction'] for e in candidates),'confirmed_exact_animation_events':0,'source_videos_modified':False,'schema_validation_passed':True}
    save(out/'summary.json',summary)
    save(out/'schema-description.json',{'version':'kinetic-text-events-v1','event_id':'source hash + first observed timestamp + OCR block index + OCR text','tracking':'Exact normalized text and max normalized box-coordinate difference <= 0.05 in consecutive sampled frames only. No fuzzy text correction.','timing':'first/last observed are evidence timestamps, not exact display interval; zero observed span means one sample, not zero display duration','classification':'Large text is a candidate heuristic, not proof of decorative overlay. Small decorative text can remain in screen-text-events.jsonl.','style':'Unknown source fonts, colors, effects and animation parameters stay null. Region palette includes background.','observations':'Every event retains each raw OCR observation, confidence, image hash, polygon and normalized coordinates.','review':'No event is promoted to verified training data automatically.'})
    report=f'''# 花字专项数据集

已处理 {videos} 个视频，将 {observations} 条 OCR 观测整理为 {len(events)} 条屏幕文字事件，其中 {len(candidates)} 条为大字/花字候选，涉及 {summary['videos_with_candidates']} 个视频。小字未丢弃，保存在全量事件表中。

## 数据入口

- `kinetic-text-candidates.jsonl`：花字候选事件，一行一条，含原片路径及哈希、识别文字、首次/末次观察时间、逐帧位置/大小/颜色区域、证据图与置信度、样式及动画字段、复核状态。
- `screen-text-events.jsonl`：全部屏幕文字事件，保留普通字幕、标识与可能的小型花字。
- `review-queue.json`：待校对清单。
- `schema-description.json`：字段和关联规则说明。

这次完成的是独立的结构化花字候选数据，不只是图片列表。所有记录可追溯到具体图片和原片时间点。

## 使用边界

首次/末次观察时间不等于精确出现/消失时间。相邻抽样图之间可能发生消失再出现，OCR 也可能漏检；不把采样跨度当作连续显示时长。动画名称、轨迹、精确字体与描边参数未确认时留空。文字框变化可能来自 OCR 抖动，不直接当作动画。

大字不一定是花字，招牌也可能被识别；较小的装饰字也可能未被候选规则选中。{summary['candidate_events_needing_text_review']} 条候选至少有一次低于 0.8 分的 OCR 观测，需校对。未自动标成可靠训练真值，未修改原视频或重新导出。
'''
    (out/'README.md').write_text(report,encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
