"""Reproducible lettering revision of the existing maternity-list edit."""
import json
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import export_video
import build_draft
from render_assembly import assemble
from aicut.media_integrity import audit_decode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--style', choices=['sticker', 'journal'], default='sticker')
    args = ap.parse_args()
    name = '学习库驱动-待产衣物清单-' + ('学习库便签花字版' if args.style == 'journal' else '动态花字版')
    source = ROOT / '03-scripts/学习库驱动-待产衣物清单-学习规则调整.sheet.json'
    sheet = json.loads(source.read_text(encoding='utf-8'))
    sheet['project'] = name
    sheet['kinetic_text_style'] = args.style
    sheet['render_pipeline']['title_renderer'] = 'ass'
    specs = [
        (0, 4.6, '待产衣物清单', '准备篇', '数量 / 尺码 / 季节', ''),
        (4.96, 8.16, '59码 · 3件', '半背衣', '', ''),
        (10.08, 13.8, '厚一点 · 3件', '联体衣', '原片条件：10月份以后', ''),
        (24.52, 29.08, '2—3双', '小袜子', '', ''),
        (29.08, 32.28, '袜子翻过来', '穿前注意', '', 'warning'),
        (32.28, 36.88, '注意袜内线头', '重点提醒', '', 'warning'),
        (43.04, 46.4, '70 × 90', '尺寸重点', '', ''),
        (49, 53.52, '85×85 / 90×90', '尺寸二选一', '', ''),
        (72.64, 75.24, '准备 3 条', '清单收尾', '收藏备用', ''),
    ]
    sheet['kinetic_text'] = [dict(start=a, end=b, title=t, label=l, subtitle=s,
        variant=v, kind='keyword', animation='pop',
        provenance='assistant_applied_unreviewed',
        reason='按现有成片转写时间对齐，仅强调原片信息；字幕正文未修改')
        for a,b,t,l,s,v in specs]
    sheet['lettering_revision'] = {'source_sheet': str(source),
        'status': 'assistant_applied_unreviewed',
        'scope': 'replace plain title overlays with animated vector lettering; preserve cuts and audio'}
    if args.style == 'journal':
        sources = [('content-library/styles/koubo-zhishi.json',
                    '专业清晰、数字关键词高亮、避免字幕挡脸；花字默认关闭由本次用户明确要求覆盖'),
                   ('content-library/architectures/jiaocheng-jiaoxue.json',
                    '关键参数可视化、步骤回顾、收藏提示；不添加库中示例口播'),
                   ('content-library/visuals/manifest.json',
                    '数字强调和警示按不同语义分类；没有复制图库图片')]
        sheet['lettering_revision']['evidence'] = [dict(source=p,
            sha256=hashlib.sha256((ROOT/p).read_bytes()).hexdigest(), applied=rule,
            authority='local_design_reference_not_human_gold') for p,rule in sources]
        sheet['lettering_revision']['designer_choices'] = ['奶油纸张色、鼠尾草绿、低饱和玫瑰色',
            '统一便签框架，数字放大，提醒圈注，200毫秒上移入场']
        sheet['lettering_revision']['not_applied'] = ['检索到的历史任务文字不作为优质样本',
            '外网蒸馏的剪辑频率不是本片硬指标', '没有训练新模型或把自动观察写成人工金标']
    problems = build_draft.quality_gates(sheet)
    if problems:
        raise RuntimeError('; '.join(problems))
    sheet_path = ROOT / '03-scripts' / (name + '.sheet.json')
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding='utf-8')
    rendered, assembly = assemble(sheet, ROOT, export_video.build_command, export_video.resolve)
    out = ROOT / '07-output' / (name + '.mp4')
    staged = out.with_suffix('.partial.mp4')
    cmd = export_video.build_command(rendered, staged, True)
    print(f'[花字] 9组 {args.style} 花字，开始导出', flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    report = {'returncode': proc.returncode, 'stderr': proc.stderr, 'assembly': assembly,
              'sheet': str(sheet_path), 'command': cmd}
    (ROOT / 'work' / (name + '.render.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if proc.returncode:
        raise RuntimeError(proc.stderr[-1500:])
    audit = audit_decode(staged)
    expected = sum((c['end']-c['start'])/c.get('speed',1) for c in sheet['clips'])
    if not audit['complete'] or abs(export_video.probe_duration(str(staged))-expected) > .2:
        raise RuntimeError('output decode/duration validation failed')
    os.replace(staged, out)
    report['decode'] = audit
    report['output'] = str(out)
    (ROOT / 'work' / (name + '.render.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    shots = ROOT / '07-output/花字截图' / ('学习库便签花字版' if args.style == 'journal' else '动态花字版')
    shots.mkdir(parents=True, exist_ok=True)
    for stamp in (1, 5.8, 30, 34, 50, 73.5):
        subprocess.run([cmd[0], '-hide_banner','-loglevel','error','-y','-ss',str(stamp),
                        '-i',str(out),'-frames:v','1',str(shots/f'{stamp:g}秒.png')], check=True)
    print(f'[完成] {out}', flush=True)


if __name__ == '__main__':
    main()
