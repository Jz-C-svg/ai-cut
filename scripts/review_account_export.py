"""Create balanced review batches and safely merge human-reviewed labels."""
from __future__ import annotations
import argparse
import json
import random
import shutil
import wave
from pathlib import Path

import numpy as np


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    tmp.replace(path)


def balanced_by_source(rows: list[dict], limit: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    pending = {}
    for row in rows:
        label = row.get("decision_label") or row.get("manual_label") or {}
        if label.get("status") == "reviewed":
            continue
        pending.setdefault(row.get("source_sha256", ""), []).append(row)
    for group in pending.values():
        rng.shuffle(group)
    selected = []
    source_ids = list(pending)
    rng.shuffle(source_ids)
    while source_ids and len(selected) < limit:
        keep = []
        for sha in source_ids:
            if pending[sha] and len(selected) < limit:
                selected.append(pending[sha].pop())
            if pending[sha]:
                keep.append(sha)
        source_ids = keep
    return selected


def create_batch(dataset: Path, output: Path, *, effect_limit: int = 50,
                 audio_limit: int = 50, seed: int = 20260927) -> dict:
    effect_file = dataset / "production-visual/kinetic-text/effect-decision-v1/decision-review-candidates.jsonl"
    audio_file = dataset / "production-audio/audio-event-v1/audio-event-candidates.jsonl"
    effects = balanced_by_source(read_jsonl(effect_file), effect_limit, seed)
    audios = balanced_by_source(read_jsonl(audio_file), audio_limit, seed + 1)
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8-sig"))
    duration_by_sha = {row.get("sha256"): float(row.get("duration", 0)) for row in manifest if row.get("kind") == "video"}
    batch = []
    for row in effects:
        batch.append({"modality": "effect_timing", "example_id": row["example_id"],
                      "source_sha256": row["source_sha256"], "source_video": row["source_video"],
                      "time": row["time"], "evidence_frame": row.get("evidence_frame"),
                      "duration": duration_by_sha.get(row["source_sha256"]),
                      "observed_screen_text": row.get("observed_screen_text", ""),
                      "transcript_context": row.get("transcript_context", ""),
                      "candidate_basis": row.get("candidate_basis"),
                      "decision_label": row.get("decision_label", {})})
    preview_dir = output / "audio-preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for row in audios:
        with np.load(row["clip_file"]) as archive:
            pcm = np.clip(archive["pcm"], -1.0, 1.0)
            rate = int(archive["sample_rate"])
        preview_path = preview_dir / (row["example_id"] + ".wav")
        with wave.open(str(preview_path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes((pcm * 32767).astype("<i2").tobytes())
        batch.append({"modality": "audio_event", "example_id": row["example_id"],
                      "source_sha256": row["source_sha256"], "source_video": row["source_video"],
                      "start": row["start"], "end": row["end"], "clip_file": row["clip_file"],
                      "playable_audio": str(preview_path),
                      "transcript_context": row.get("transcript_context", ""),
                      "automatic_candidate_flags": row.get("automatic_candidate_flags", []),
                      "features": row.get("features", {}), "manual_label": row.get("manual_label", {})})
    write_jsonl(output / "review-batch.jsonl", batch)
    html_rows = []
    for row in batch:
        html_row = dict(row)
        html_row["video_uri"] = Path(row["source_video"]).as_uri()
        if row.get("evidence_frame"):
            html_row["frame_uri"] = Path(row["evidence_frame"]).as_uri()
        if row.get("playable_audio"):
            html_row["audio_uri"] = Path(row["playable_audio"]).as_uri()
        html_rows.append(html_row)
    payload = json.dumps(html_rows, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    page = _review_html(payload)
    (output / "review.html").write_text(page, encoding="utf-8")
    guide = {
        "created_for": "Human review only. Do not infer labels from OCR/ASR/heuristic flags alone.",
        "effect_fields": {"should_add_text": "required boolean", "should_add_effect": "required boolean",
                          "best_timing_start/end": "required source times when should_add_text=true; null otherwise",
                          "reason": "required evidence-based explanation", "reviewer": "your reviewer identity"},
        "audio_fields": {"contains_speech/music/sfx": "set each to true or false after listening",
                         "class": "speech/music/sfx/mixed/other", "sfx_type": "optional precise type",
                         "reason": "required explanation", "reviewer": "your reviewer identity"},
        "preservation": "This batch is separate from source labels. Use review_account_export.py --apply only after review.",
    }
    (output / "review-guide.json").write_text(json.dumps(guide, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"effect_candidates": len(effects), "audio_candidates": len(audios),
            "distinct_effect_videos": len({r["source_sha256"] for r in effects}),
            "distinct_audio_videos": len({r["source_sha256"] for r in audios}),
            "batch": str(output / "review-batch.jsonl"), "review_page": str(output / "review.html"),
            "labels_filled_automatically": 0}


def _review_html(payload: str) -> str:
    return '''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>账号导出人工复核</title><style>body{font:15px system-ui;margin:0;background:#f4f5f7;color:#202124}header{position:sticky;top:0;background:#fff;padding:12px 18px;box-shadow:0 1px 6px #0002;z-index:2;display:flex;gap:12px;align-items:center;flex-wrap:wrap}.card{background:white;margin:16px auto;padding:18px;border-radius:12px;max-width:1000px;box-shadow:0 1px 5px #0001}.media{display:grid;grid-template-columns:1fr 1fr;gap:12px}.media video,.media img,.media audio{max-width:100%;width:100%;max-height:520px}.ctx{white-space:pre-wrap;background:#f7f8fa;padding:10px;border-radius:8px}.form{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}.form label{display:grid;gap:4px}textarea{width:min(700px,95%);height:56px}button,input,select{font:inherit;padding:6px}small{color:#555}@media(max-width:700px){.media{grid-template-columns:1fr}}</style>
<header><strong>账号导出复核</strong><span id="count"></span><label>复核人 <input id="reviewer" placeholder="填写姓名/标识"></label><button id="export">导出已复核标签 JSONL</button><span id="status"></span></header><main id="items"></main>
<script>const rows=__PAYLOAD__;const root=document.getElementById('items');const reviewer=document.getElementById('reviewer');
function el(tag,props={},text=''){const n=document.createElement(tag);Object.assign(n,props);if(text)n.textContent=text;return n}
function select(options){const s=el('select');for(const [v,t] of options){const o=el('option',{value:v},t);s.appendChild(o)}return s}
const yesNo=[['','选择'],['true','是'],['false','否']];const state=JSON.parse(localStorage.getItem('aicut-review-state')||'{}');reviewer.value=localStorage.getItem('aicut-reviewer')||'';
function persist(){localStorage.setItem('aicut-review-state',JSON.stringify(state));localStorage.setItem('aicut-reviewer',reviewer.value)}reviewer.oninput=persist;
for(const r of rows){const card=el('section',{className:'card'});card.appendChild(el('h2',{},(r.modality==='effect_timing'?'花字/特效':'音频')+' · '+r.example_id));card.appendChild(el('small',{},r.source_video+' · '+(r.time??(r.start+'–'+r.end))+' 秒 · '+r.source_sha256));
 const grid=el('div',{className:'media'});if(r.modality==='effect_timing'){const v=el('video',{controls:true,preload:'metadata'});v.src=r.video_uri;v.onloadedmetadata=()=>{v.currentTime=Math.max(0,(r.time||0)-3)};grid.appendChild(v);if(r.frame_uri){const im=el('img',{alt:'证据帧'});im.src=r.frame_uri;grid.appendChild(im)}}else{const a=el('audio',{controls:true,preload:'metadata'});a.src=r.audio_uri;grid.appendChild(a);const info=el('div',{className:'ctx'},JSON.stringify(r.features,null,2));grid.appendChild(info)}card.appendChild(grid);
 card.appendChild(el('div',{className:'ctx'},'口播上下文：'+(r.transcript_context||'（无）')+'\n画面文字：'+(r.observed_screen_text||'（无）')+'\n候选依据：'+(r.candidate_basis||r.automatic_candidate_flags||'（无）')));
 const form=el('div',{className:'form'});const controls={};function field(name,node){const l=el('label',{},name);l.appendChild(node);form.appendChild(l);controls[name]=node;node.onchange=persist;node.oninput=persist}
 if(r.modality==='effect_timing'){field('是否加花字',select(yesNo));field('是否加特效',select(yesNo));field('起始秒',el('input',{type:'number',step:'0.01'}));field('结束秒',el('input',{type:'number',step:'0.01'}));field('效果类型',el('input',{placeholder:'如：强调/转场/无'}))}
 else{field('语音',select(yesNo));field('音乐',select(yesNo));field('音效',select(yesNo));field('声音类别',select([['','选择'],['speech','语音'],['music','音乐'],['sfx','音效'],['mixed','混合'],['other','其他']]));field('音效类型',el('input',{placeholder:'如：提示音/掌声/环境声'}))}
 const reason=el('textarea',{placeholder:'写下听看证据和判断理由'});field('复核理由',reason);for(const [key,node] of Object.entries(controls)){const saved=state[r.example_id]?.[key];if(saved!==undefined)node.value=saved;node.addEventListener('input',()=>{state[r.example_id]??={};state[r.example_id][key]=node.value;persist()});node.addEventListener('change',()=>{state[r.example_id]??={};state[r.example_id][key]=node.value;persist()})}root.appendChild(card);r._controls=controls}
document.getElementById('count').textContent=rows.length+' 条候选';document.getElementById('export').onclick=()=>{const out=[];for(const r of rows){const c=r._controls,reviewerName=reviewer.value.trim();if(!c||!c['复核理由'].value.trim())continue;let item={...r};delete item._controls;if(r.modality==='effect_timing'){const t=c['是否加花字'].value,e=c['是否加特效'].value;if(!t||!e)continue;item.decision_label={...(r.decision_label||{}),status:'reviewed',should_add_text:t==='true',should_add_effect:e==='true',best_timing_start:t==='true'?Number(c['起始秒'].value):null,best_timing_end:t==='true'?Number(c['结束秒'].value):null,effect_type:c['效果类型'].value||null,reason:c['复核理由'].value.trim(),reviewer:reviewerName}}else{const speech=c['语音'].value,music=c['音乐'].value,sfx=c['音效'].value,kind=c['声音类别'].value;if(!speech||!music||!sfx||!kind)continue;item.manual_label={...(r.manual_label||{}),status:'reviewed',contains_speech:speech==='true',contains_music:music==='true',contains_sfx:sfx==='true',class:kind,sfx_type:c['音效类型'].value||null,reason:c['复核理由'].value.trim(),reviewer:reviewerName}}out.push(item)}if(!out.length){alert('请先填写完整标签、理由和复核人');return}const blob=new Blob([out.map(x=>JSON.stringify(x)).join('\n')+'\n'],{type:'application/x-ndjson'});const a=el('a',{href:URL.createObjectURL(blob),download:'reviewed-labels.jsonl'});a.click();document.getElementById('status').textContent='已导出 '+out.length+' 条';};
</script></main></html>'''.replace("__PAYLOAD__", payload)


def _validate_review(row: dict) -> dict:
    label = row.get("decision_label") if row.get("modality") == "effect_timing" else row.get("manual_label")
    if not isinstance(label, dict) or label.get("status") != "reviewed":
        raise ValueError("every submitted row must have status=reviewed")
    if not str(label.get("reviewer", "")).strip() or not str(label.get("reason", "")).strip():
        raise ValueError("reviewer and reason are required")
    if row.get("modality") == "effect_timing":
        for key in ("should_add_text", "should_add_effect"):
            if not isinstance(label.get(key), bool):
                raise ValueError(key + " must be an explicit boolean")
        start, end = label.get("best_timing_start"), label.get("best_timing_end")
        if label["should_add_text"]:
            if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                    or not 0 <= float(start) <= float(row["time"]) <= float(end)):
                raise ValueError("text timing must be a valid source interval containing the reviewed candidate time")
            if float(end) > float(row.get("duration", float("inf"))):
                raise ValueError("text timing exceeds source duration")
        elif start is not None or end is not None:
            raise ValueError("timing must be null when should_add_text is false")
    elif row.get("modality") == "audio_event":
        for key in ("contains_speech", "contains_music", "contains_sfx"):
            if not isinstance(label.get(key), bool):
                raise ValueError(key + " must be an explicit boolean")
        if label.get("class") not in {"speech", "music", "sfx", "mixed", "other"}:
            raise ValueError("audio class must be speech/music/sfx/mixed/other")
        present = [name for name in ("contains_speech", "contains_music", "contains_sfx") if label[name]]
        expected = present[0].replace("contains_", "") if len(present) == 1 else "mixed" if present else "other"
        if label["class"] != expected:
            raise ValueError("audio class must agree with the speech/music/sfx presence labels")
    else:
        raise ValueError("unknown review modality")
    return label


def apply_labels(dataset: Path, completed_path: Path) -> dict:
    effects_path = dataset / "production-visual/kinetic-text/effect-decision-v1/decision-review-candidates.jsonl"
    audio_path = dataset / "production-audio/audio-event-v1/audio-event-candidates.jsonl"
    source_by_modality = {"effect_timing": (effects_path, read_jsonl(effects_path)),
                          "audio_event": (audio_path, read_jsonl(audio_path))}
    lookup = {modality: {str(row["example_id"]): row for row in rows}
              for modality, (_, rows) in source_by_modality.items()}
    submitted = read_jsonl(completed_path)
    seen = set()
    for row in submitted:
        modality, example_id = row.get("modality"), str(row.get("example_id", ""))
        key = (modality, example_id)
        if key in seen:
            raise ValueError("duplicate submitted example: " + example_id)
        seen.add(key)
        source = lookup.get(modality, {}).get(example_id)
        if source is None or row.get("source_sha256") != source.get("source_sha256"):
            raise ValueError("unknown example or source hash mismatch: " + example_id)
        if float(row.get("time", row.get("start", -1))) != float(source.get("time", source.get("start", -2))):
            raise ValueError("candidate timestamp mismatch: " + example_id)
        label = _validate_review(row)
        field = "decision_label" if modality == "effect_timing" else "manual_label"
        current = source.get(field) or {}
        if current.get("status") == "reviewed":
            raise ValueError("refusing to overwrite reviewed label: " + example_id)
        source[field] = label
    for modality, (path, rows) in source_by_modality.items():
        if any((r.get("decision_label") or {}).get("status") == "reviewed" for r in rows) if modality == "effect_timing" else any((r.get("manual_label") or {}).get("status") == "reviewed" for r in rows):
            backup = path.with_suffix(path.suffix + ".pre-review.bak")
            if not backup.exists():
                shutil.copy2(path, backup)
            write_jsonl(path, rows)
    return {"applied_labels": len(submitted), "effects_reviewed": sum(r.get("modality") == "effect_timing" for r in submitted),
            "audio_reviewed": sum(r.get("modality") == "audio_event" for r in submitted),
            "backup_suffix": ".pre-review.bak", "index_rebuild_required": True}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--create-batch", action="store_true")
    p.add_argument("--apply", help="Completed review-batch JSONL after a reviewer edits every chosen label")
    p.add_argument("--out", default=str(Path("work/account-export-review-20260927")))
    p.add_argument("--effect-limit", type=int, default=50)
    p.add_argument("--audio-limit", type=int, default=50)
    p.add_argument("--seed", type=int, default=20260927)
    args = p.parse_args()
    if args.create_batch == bool(args.apply):
        p.error("choose exactly one of --create-batch or --apply")
    if args.create_batch:
        result = create_batch(Path(args.dataset), Path(args.out), effect_limit=args.effect_limit,
                              audio_limit=args.audio_limit, seed=args.seed)
    else:
        result = apply_labels(Path(args.dataset), Path(args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
