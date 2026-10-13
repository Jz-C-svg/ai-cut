"""Reproduce the failed local sample with the repaired evidence gate."""
import json,time,urllib.request
from pathlib import Path
from grounded_flower_plan import check_plan,materialize,write_markdown,ROLES

ROOT=Path(__file__).resolve().parents[1]
PREV=ROOT/'work/local-kinetic-generation-20260924'
OUT=ROOT/'work/local-kinetic-generation-20260924-repaired'
OUT.mkdir(parents=True,exist_ok=True)
def save(name,value): (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
evidence=json.loads((PREV/'input-evidence.json').read_text(encoding='utf-8'))
# Case-specific reviewed text suspicions, NOT corrected transcript or generic ASR detector.
tokens=['扶植机','扶机','去漆','手机','旗上','旗中','旗下','机子','骑中','骑上','内功','两只']
issues=[{'segment_id':s['id'],'raw_text':s['text'],'reason':'文本复核发现疑似同音或术语错词，须回听；未自动替换','suspected_tokens':[t for t in tokens if t in s['text']],'verified_correction':None} for s in evidence['segments'] if any(t in s['text'] for t in tokens)]
uncertain={x['segment_id'] for x in issues}
save('asr-review-queue.json',issues)
context=dict(evidence,asr_review_queue=issues)
save('input-evidence.json',context)
prompt='''根据证据整理口播方案。只输出 JSON，顶层只有 groups。每组只有 segment_ids、role、flower 三个字段。所有原稿 id 按原顺序恰好使用一次，可以合并相邻片段，不能遗漏。role 只能从角色列表选择，不得具体标号某个解剖位置。flower 为 null 或 {"text":"原话逐字摘录2到12字","segment_id":"本组的原稿id","style_reference_id":"已有样式id"}。总花字数不超过3且不超过分组数的一半向上取整，优先只选2条明确动作/注意事项。任何 asr_review_queue 里的 segment_id 都不能用于花字，但仍要完整保留在分组里。不得猜测纠正原话，不生成标题、理由、时间、医疗结论或动画参数。将多个位置演示归入动作演示，不要把后面的注意事项当成第三个位置。不需要花字的段落用 null。角色列表：'''+json.dumps(sorted(ROLES),ensure_ascii=False)+'\n证据：'+json.dumps(context,ensure_ascii=False)
(OUT/'prompt.txt').write_text(prompt,encoding='utf-8')
messages=[{'role':'user','content':prompt}]
# Explicit source excerpts eliminate free-form paraphrase and malformed flower arrays.
choices=[None]
for s in evidence['segments']:
    if s['id'] not in uncertain:
        for excerpt in ('先平躺','微微抬头','把指甲剪一下'):
            if excerpt in s['text']:
                choices.append({'text':excerpt,'segment_id':s['id'],'style_reference_id':'visual-style-2'})
schema={'type':'object','additionalProperties':False,'required':['groups'],'properties':{'groups':{'type':'array','minItems':1,'items':{'type':'object','additionalProperties':False,'required':['segment_ids','role','flower'],'properties':{'segment_ids':{'type':'array','minItems':1,'items':{'type':'string','enum':[s['id'] for s in evidence['segments']]}},'role':{'type':'string','enum':sorted(ROLES)},'flower':{'enum':choices}}}}}}
messages[0]['content']+='\n最终硬限制：花字至多2条，优先准备动作和剪指甲注意事项，避免重复。以下是本样本文本复核选出的候选片语（未回听，不代表医学核验）；必须原样选择或用null：'+json.dumps(choices,ensure_ascii=False)
save('output-schema.json',schema)
(OUT/'prompt.txt').write_text(messages[0]['content'],encoding='utf-8')
started=time.monotonic()
for attempt in range(1,3):
    payload={'model':'gemma4:12b','think':False,'stream':False,'format':schema,'keep_alive':'30m','messages':messages,'options':{'temperature':0.1,'num_ctx':16384,'num_predict':2500}}
    request=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=900) as r: response=json.load(r)
    save(f'raw-response-{attempt}.json',response)
    content=response['message']['content']
    try:
        plan=json.loads(content);errors=check_plan(plan,evidence,uncertain)
    except (ValueError,TypeError,KeyError) as ex: errors=[str(ex)]
    if response.get('done_reason')!='stop': errors.append('generation incomplete')
    save(f'checks-{attempt}.json',{'errors':errors})
    if not errors: break
    messages.extend([{'role':'assistant','content':content},{'role':'user','content':'修复校验错误，仅输出完整 JSON：'+json.dumps(errors,ensure_ascii=False)}])
summary={'model':'gemma4:12b','elapsed_seconds':round(time.monotonic()-started,2),'attempts':attempt,'generation_gate_passed':not errors,'errors':errors,'semantic_review':'pending','release_ready':False,'source_segments':len(evidence['segments']),'asr_flagged_segments':len(issues),'video_exported':False}
if not errors:
    result=materialize(plan,evidence,uncertain);save('generated-plan.json',plan);save('evidence-bound-plan.json',result)
    write_markdown(result,OUT/'修复后生成方案.md')
    summary.update(output_groups=len(plan['groups']),flower_count=sum(g['flower'] is not None for g in plan['groups']))
save('summary.json',summary);print(json.dumps(summary,ensure_ascii=False))
