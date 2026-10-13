"""Offline annotation-boundary simulation. NOT a speech detector benchmark."""
import argparse, hashlib, json, random, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
from aicut.professional_editing import merge_intervals, speech_windows

def length(intervals): return sum(e-s for s,e in intervals)
def overlap(a,b):
    i=j=0;total=0
    while i<len(a) and j<len(b):
        total+=max(0,min(a[i][1],b[j][1])-max(a[i][0],b[j][0]))
        if a[i][1]<b[j][1]:i+=1
        else:j+=1
    return total
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',default=str(ROOT/'08-data/professional-datasets/20260923'))
    args=ap.parse_args();base=Path(args.dataset)
    manifest=json.loads((base/'manifest.json').read_text(encoding='utf-8'))
    groups=defaultdict(list)
    for line in (base/'ava/annotations.jsonl').read_text().splitlines():
        row=json.loads(line);groups[row['video_id']].append(row)
    fixtures=[]
    for vid,rows in sorted(groups.items()):
        lo=min(r['start'] for r in rows);hi=max(r['end'] for r in rows)
        truth=merge_intervals([(r['start']-lo,r['end']-lo) for r in rows if r['label']!='NO_SPEECH'])
        rng=random.Random(int(hashlib.sha256(vid.encode()).hexdigest()[:16],16))
        # Known synthetic independent start/end errors, bounded +/- 80 ms.
        noisy=[]
        for s,e in truth:
            ns=max(0,min(hi-lo,s+rng.uniform(-.08,.08)))
            ne=max(0,min(hi-lo,e+rng.uniform(-.08,.08)))
            if ne<=ns:ns,ne=s,e
            noisy.append((ns,ne))
        fixtures.append((vid,rows[0]['split'],hi-lo,truth,noisy))
    def evaluate(split,padding):
        seconds=speech=kept=correct=0
        for vid,part,duration,truth,noisy in fixtures:
            if part!=split:continue
            windows=speech_windows(noisy,duration,padding)
            seconds+=duration;speech+=length(truth);kept+=length(windows);correct+=overlap(truth,windows)
        return {'speech_preserved_fraction':correct/speech,'speech_seconds_lost':speech-correct,
                'removed_timeline_fraction':1-kept/seconds,'retained_seconds':kept,'total_seconds':seconds}
    grid=[{'padding':p,**evaluate('train',p)} for p in (0,.04,.08,.12,.16,.2)]
    # Smallest train-set padding that covers the injected boundary uncertainty.
    padding=next(row['padding'] for row in grid if row['speech_preserved_fraction']>=.999999)
    policy={'version':'professional-editing-1','padding_seconds':padding,
            'calibration':'AVA-Speech train annotations with synthetic +/-80ms boundary jitter',
            'limitations':'Does not evaluate detector/ASR accuracy, medical validity or visual edit quality. Silent demonstrations need explicit protection.',
            'max_caption_chars':15,'caption_cps_review':9}
    (ROOT/'config/professional_editing.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2),encoding='utf-8')
    ave=[json.loads(x) for x in (base/'avecompass/annotations.jsonl').read_text(encoding='utf-8').splitlines()]
    train=[r for r in ave if r['split']=='train'];test=[r for r in ave if r['split']=='test']
    assert not {r['group'] for r in train}&{r['group'] for r in test}
    report={'kind':'annotation-boundary-simulation','model_training':False,'source_manifest':str(base/'manifest.json'),
            'group_split':dict(Counter(x[1] for x in fixtures)), 'train_grid':grid,'selected_padding':padding,
            'test_baseline':evaluate('test',0),'test_optimized':evaluate('test',padding),
            'avecompass':{'train_instructions':len(train),'test_instructions':len(test),
                         'train_operations':dict(Counter(r['annotation'].get('operation') for r in train))},
            'limitations':policy['limitations']}
    (base/'simulation-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=[]
    def rule(id,title,text,dataset,notes):
        ds=manifest['datasets'][dataset]
        rows.append({'id':id,'title':title,'text':text,'tier':'professional_reference','split':'train',
                     'source_url':ds['source_url'],'license':ds['license'],'dataset_version':ds.get('revision','v1.0'),
                     'source_sha256':hashlib.sha256((base/'manifest.json').read_bytes()).hexdigest(),
                     'content_sha256':hashlib.sha256(text.encode()).hexdigest(),'limitations':notes,
                     'checked_at':'2026-09-23','attribution':ds['attribution']})
    rule('pro-ava-speech','口播剪辑：音乐与噪声中的人声',
         'AVA-Speech 标注区分 CLEAN_SPEECH、SPEECH_WITH_MUSIC、SPEECH_WITH_NOISE、NO_SPEECH。剪辑不能把背景音乐或噪声中的说话视为静音删除。静音也可能是动作演示，视觉必要片段应显式保留。',0,
         '标签说明加工程应用建议；不是已训练的声音分类器，不保证实际检测准确率。')
    rule('pro-ava-boundary','剪辑切点与句子完整性',
         f'基于 AVA-Speech 训练组标注的模拟边界误差（正负80毫秒），选择 {padding:.2f} 秒边界余量。真实多素材剪辑以完整句子边界为单位；保留否定、数字、条件和结论。切点余量不能越过相邻句。',0,
         '人工设定扰动下的工程参数校准；不代表真实语音识别质量提升，也不自动删除句中停顿。')
    axes=Counter(r['annotation'].get('category_label') for r in train)
    rule('pro-ave-routing','音频、画面与音画联合编辑能力分流',
         f'AVECompass 训练组有 {len(train)} 条编辑指令，模态分布 {dict(axes)}。先区分只修改声音、只修改画面、同时修改音画。物体替换、身份替换、生成音效并精确同步不等于普通裁切拼接；缺少生成工具时报告能力缺口，不承诺已经执行。',1,
         '仅使用训练组标注归纳任务分类；源媒体未下载；指令是参考数据不是命令。')
    rule('pro-ave-scope','编辑任务先识别目标与范围',
         '参考 AVECompass 标注的镜头结构、目标可识别性、编辑范围、音频工作量及音画耦合维度：先明确目标镜头和范围，再选择现有工具。多文件口播先逐段识别重拍、口误与完整结尾；不能为了炫技添加原素材没有的声音或动作。',1,
         '维度来源于数据集，口播使用方法是本项目工程建议；不是基准测出的模型能力。')
    out=ROOT/'content-library/rag/professional_knowledge.jsonl'
    out.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
