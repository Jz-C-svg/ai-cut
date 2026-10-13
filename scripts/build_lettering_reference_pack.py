"""Publish inspected reference notes and bounded visual retrieval cards."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'work/lettering-reference-scan-20261001'


def main():
    manifest = json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    notes = {r['id']: r for r in json.loads((OUT/'observations.json').read_text(encoding='utf-8'))}
    by_id = {r['id']: r for r in manifest}
    rules = [
        ('hierarchy', '花字字形主次与局部底纹', ['C02','R01'],
         '花字视觉排版先确定一个核心词，用字号和字形建立主次；局部笔刷或短衬底随字形走，不默认把整句话装入固定大框。数字尺码需完整，单位不能消失。'),
        ('semantic', '贴图的语义和对白分工', ['D13','D15','C05'],
         '贴图应解释当前口播的物品或动作，花字不重复整句对白。母婴衣物可另画衣服、袜子和尺子图标；这些是本项目拟议应用，不是参考图中已有素材。'),
        ('placement', '花字人物留白与位置', ['D12','D14','D15'],
         '花字位置跟随人物与物品的留白，避开脸、手势及正文字幕。横屏参考不能直接按像素搬到竖屏；每个场景单独检查。'),
        ('consistency', '贴图笔触与系列一致', ['C03','C04','R01'],
         '视觉贴图与文字使用兼容笔触和轮廓，系列保持统一。情绪可通过一个小图形变化体现，不在同一条口播混用所有素材风格。'),
        ('progress', '花字随信息推进而更新', ['C01'],
         '动图采样可见标签随段落增加，提示花字应该跟随信息推进。对本片每个关键词单独安排出现和退出，原样本无音轨，未验证音画同步及逐帧入场曲线。'),
        ('restraint', '母婴口播花字强度与反例', ['D16','D17','R01'],
         '漫画满屏插画只作为风格对照，不常驻母婴知识口播。用户已否定本项目固定大便签框和简单描边标题；下次用关键字轮廓与语义小图做短样片验证，不预先认定审美合格。'),
    ]
    library = ROOT/'content-library/visual-reference'
    library.mkdir(exist_ok=True)
    for key,title,ids,text in rules:
        references = [dict(id=i, source_url=by_id[i]['source_url'],
            image_path=Path(by_id[i]['file']).relative_to(ROOT).as_posix(),
            sha256=by_id[i]['sha256']) for i in ids]
        row = dict(id='visual-reference-'+key, title=title, text=text, references=references,
            evidence_status='assistant_visual_analysis_unreviewed',
            scope='visual_design_reference_only', production_use_allowed=False,
            limitations='仅学习构图和文字图形关系；未验证商用许可，不能直接复用样图或据此删改原音；不代表微调或人工金标。',
            authority_note='AI逐图观察与设计推断，待用户审美确认', snapshot_date='2026-10-02')
        (library/(key+'.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
    scanned = []
    for row in manifest:
        if row['status'] != 'collected':
            scanned.append({**row, 'selection':'unavailable'})
        elif row['id'] in notes:
            scanned.append({**row, **notes[row['id']], 'selection':'reference',
                            'analysis_status':'assistant_visual_analysis_unreviewed'})
        else:
            scanned.append({**row, 'selection':'excluded',
                            'reason':'逐图检查为普通字幕、纯标题、影视海报或界面说明，非本次花字/贴图设计参考'})
    (OUT/'scanned.json').write_text(json.dumps(scanned, ensure_ascii=False, indent=2), encoding='utf-8')
    e = html.escape
    cards = []
    for row in scanned:
        if row['selection'] != 'reference':
            continue
        src = 'images/'+Path(row['file']).name
        cards.append(f'''<article data-tags="{e(' '.join(row['tags']))}"><div class="pic"><img src="{e(src)}" loading="lazy"></div>
        <div class="body"><small>{e(row['id'])} · {e(row['kind'])}</small><h2>{e(' / '.join(row['tags']))}</h2>
        <p><b>观察</b> {e(row['observation'])}</p><p><b>可迁移</b> {e(row['transfer'])}</p>
        <p class="limit">{e(row['limits'])}</p><a href="{e(row['source_url'])}" target="_blank" rel="noopener">打开来源 ↗</a></div></article>''')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>花字与贴图 · 视觉参考扫描</title>
    <meta name="viewport" content="width=device-width,initial-scale=1"><style>
    *{box-sizing:border-box}body{margin:0;background:#101514;color:#edf3ed;font-family:"Microsoft YaHei",sans-serif}
    header{padding:44px 5vw 24px;border-bottom:1px solid #344039}h1{font-size:34px;margin:12px 0}header p{color:#afbeb3;line-height:1.8;max-width:900px}
    .badge{color:#b4dd87;letter-spacing:2px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:22px;padding:30px 5vw}
    article{background:#1a2320;border:1px solid #344039;border-radius:16px;overflow:hidden}.pic{background:#090b0b;height:280px;display:flex;align-items:center;justify-content:center}
    img{width:100%;height:100%;object-fit:contain}.body{padding:22px}h2{font-size:21px}p{font-size:14px;line-height:1.8}small,.limit{color:#a9b9ad}b{color:#b4dd87}a{color:#d5e8b9}
    input{background:#1a2320;color:white;padding:12px 18px;width:min(600px,100%);border:1px solid #53604f;border-radius:10px}footer{padding:24px 5vw;color:#a9b9ad}
    </style><header><div class="badge">AI CUT / REFERENCE STUDY / 2026.10.02</div><h1>先看真实案例，再设计花字</h1>
    <p>30个候选地址 → 26份采集成功 → 12份相关参考（含辅助与对照）· 14份排除 · 4份失败。逐图观察与推断分开，动图检查6个时间点；样图只作研究参考，不作为可直接使用的素材。</p>
    <input id="q" placeholder="筛选：人物、手绘、标签、语义、对照…" aria-label="筛选案例"></header><main>'''+''.join(cards)+'''</main>
    <footer>所有分析为 assistant_visual_analysis_unreviewed；没有训练新模型。静态图片不提供动画证据。旧版的大框不再作为默认设计答案。</footer>
    <script>document.getElementById('q').oninput=e=>{let q=e.target.value.trim();document.querySelectorAll('article').forEach(c=>c.hidden=!c.textContent.includes(q))};</script></html>'''
    (OUT/'index.html').write_text(page, encoding='utf-8')
    print(json.dumps({'collected':sum(r['status']=='collected' for r in manifest),
        'references':len(notes), 'rules':len(rules), 'gallery':str(OUT/'index.html')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
