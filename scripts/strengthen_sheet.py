"""Create a new, audited production sheet; leave previous edits intact."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'lib'))
from aicut.content_guard import correct_captions
from aicut.visual_layout import prepare_cards
import build_draft

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--sheet',required=True);ap.add_argument('--name',required=True);ap.add_argument('--rules',required=True)
    args=ap.parse_args();build_draft.project_name(args.name)
    path=Path(args.sheet);path=path if path.is_absolute() else ROOT/path
    sheet=json.loads(path.read_text(encoding='utf-8'))
    rules=json.loads(Path(args.rules).read_text(encoding='utf-8'))
    transcript=json.loads(Path(build_draft.resolve(sheet['subtitles_json'])).read_text(encoding='utf-8'))
    chapters=sheet['chapter_map'];corrections=[];segments=[]
    for segment in transcript['segments']:
        position=next(i for i,c in enumerate(chapters) if c['start']-.001<=segment['start']<c['end'])
        source_id=Path(sheet['clips'][position]['file']).stem.split('-')[0]
        rows,changes=correct_captions([segment],rules,source_id)
        row=rows[0];text=row['text'];row['text']='\n'.join(text[i:i+14] for i in range(0,len(text),14))
        for change in changes:change.update(timeline_start=segment['start'],timeline_end=segment['end'])
        segments.append(row);corrections.extend(changes)
    cards,audit=prepare_cards(sheet.get('kinetic_text',[]),sheet['clips'],build_draft.resolve,
                             manual_regions=[(0,1320,1080,1650)])
    sheet['kinetic_text']=cards
    sheet['required_source_ranges']=[{'file':c['file'],'start':c['start'],'end':c['end'],'label':c['chapter']} for c in sheet['clips']]
    sheet['project']=args.name;sheet['subtitles_json']=f'02-transcript/{args.name}.json'
    sheet['visual_layout_audit']=f'work/{args.name}.strengthening-audit.json'
    transcript['segments']=segments;transcript['name']=args.name
    (ROOT/sheet['subtitles_json']).write_text(json.dumps(transcript,ensure_ascii=False,indent=2),encoding='utf-8')
    problems=build_draft.quality_gates(sheet)
    if problems:raise ValueError('; '.join(problems))
    target=ROOT/'03-scripts'/f'{args.name}.sheet.json'
    target.write_text(json.dumps(sheet,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/sheet['visual_layout_audit']).write_text(json.dumps({'corrections':corrections,'layout':audit,
        'required_source_ranges':sheet['required_source_ranges'],'quality_gates':problems,
        'manual_region_basis':'manual conservative subtitle/hand zone; not detected hands',
        'corrections_review_state':'assistant_context_review_unverified'},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'sheet':str(target),'corrections':len(corrections),'cards':len(cards),'layout':audit},ensure_ascii=False))

if __name__=='__main__':main()
