"""Compile source-bound subtitle proposals; citations alone never count as effects."""
from __future__ import annotations

import copy
import hashlib
import json
import re


def text_key(text):
    return ''.join(str(text).split())


def phrase_parts(text, parts, max_chars=12):
    """Preserve every source character except layout whitespace, in order."""
    if not isinstance(parts, list) or not 2 <= len(parts) <= 8:
        raise ValueError('subtitle phrases must contain 2-8 parts')
    if any(not isinstance(p, str) or not p.strip() or '\n' in p or '\r' in p
           or len(p.strip()) > max_chars for p in parts):
        raise ValueError('subtitle phrase is empty, multiline or too long')
    parts = [p.strip() for p in parts]
    if text_key(''.join(parts)) != text_key(text):
        raise ValueError('subtitle phrases must preserve all source text in order')
    source = text_key(text)
    boundaries, cursor = [], 0
    for part in parts[:-1]:
        cursor += len(text_key(part))
        boundaries.append(cursor)
    protected = list(re.finditer(
        r'\d+(?:\.\d+)?(?:毫升|毫克|厘米|毫米|公斤|千克|小时|分钟|秒|元|岁|月|天|个|件|片|%|％)?'
        r'|[A-Za-z]+', source))
    if any(match.start() < boundary < match.end()
           for match in protected for boundary in boundaries):
        raise ValueError('subtitle phrase boundary splits a number, unit or English word')
    return parts


def subtitle_action(row, evidence, sentence, max_chars=12):
    """Only reviewed transferable subtitle rules can justify this action type."""
    if (evidence.get('category') != 'visual_decision_card'
            or evidence.get('topic_group') != '字幕'
            or evidence.get('evidence_type') != 'human_derived_transferable_rule'
            or not evidence.get('evidence_refs')):
        raise ValueError('not a source-backed transferable subtitle rule')
    if not row.get('具体改变') or not row.get('素材依据'):
        raise ValueError('missing editing reason or source grounding')
    if row.get('证据id') not in (sentence.get('知识依据') or []):
        raise ValueError('subtitle action must be cited on the source sentence')
    params = row.get('执行参数')
    if not isinstance(params, dict) or set(params) != {'type', 'phrases'}:
        raise ValueError('subtitle action requires explicit type and phrases')
    if params['type'] != 'subtitle_phrases':
        raise ValueError('unsupported learning execution action')
    return {'type': 'subtitle_phrases',
            'phrases': phrase_parts(sentence.get('句', ''), params['phrases'], max_chars)}


def compile_subtitles(intent, segments, max_chars=12):
    """Attach validated phrases after trimming/reordering, by exact text identity.

    No media is changed here. Consumers later split these phrases into timed
    events. Ambiguous or edited sentences are rejected instead of using old IDs.
    """
    from .learning_context import selected_evidence
    from .text_overlays import split_caption_timeline

    output = copy.deepcopy(segments)
    evidence = {d['id']: d for d in selected_evidence(intent.get('learning_context') or {})}
    sentences = {s.get('句号'): s for s in intent.get('逐句意图') or [] if isinstance(s, dict)}
    audit = {'schema': 'learning-subtitle-execution-1', 'compiled_count': 0,
             'entries': [], 'effect_verified': False, 'aesthetic_verified': False,
             'timing_scope': 'character-weighted estimate, not forced alignment'}
    claimed = set()
    for row in (intent.get('learning_application') or {}).get('采用') or []:
        if not isinstance(row, dict) or '执行参数' not in row:
            continue
        entry = {'evidence_id': row.get('证据id'), 'status': 'rejected'}
        try:
            ids = row.get('对应句号')
            if (not isinstance(ids, list) or len(ids) != 1
                    or type(ids[0]) is not int or ids[0] not in sentences):
                raise ValueError('subtitle action must bind exactly one source sentence')
            sentence = sentences[ids[0]]
            doc = evidence.get(row.get('证据id')) or {}
            params = subtitle_action(row, doc, sentence, max_chars)
            matches = [i for i, seg in enumerate(output)
                       if text_key(seg.get('text', '')) == text_key(sentence.get('句', ''))]
            if len(matches) != 1:
                raise ValueError('source sentence missing or ambiguous after editing')
            index = matches[0]
            if index in claimed:
                raise ValueError('conflicting subtitle actions on the same output sentence')
            candidate = dict(output[index], caption_phrases=params['phrases'])
            events = split_caption_timeline([candidate], max_chars=max_chars)
            proof = {k: doc[k] for k in ('id', 'source', 'decision_rule', 'evidence_refs') if k in doc}
            proof_hash = hashlib.sha256(json.dumps(proof, sort_keys=True,
                ensure_ascii=False).encode('utf-8')).hexdigest()
            candidate['caption_learning'] = {'evidence_id': row['证据id'],
                'evidence_sha256': proof_hash, 'source_sentence': sentence['句'],
                'status': 'compiled_pending_output_verification'}
            output[index] = candidate
            claimed.add(index)
            entry.update(status='compiled', output_index=index, source_sentence=sentence['句'],
                         phrases=params['phrases'], evidence_sha256=proof_hash,
                         expected_events=[{k: e[k] for k in ('start', 'end', 'text')} for e in events])
            audit['compiled_count'] += 1
        except (TypeError, ValueError, KeyError) as exc:
            entry['reason'] = str(exc)
        audit['entries'].append(entry)
    return output, audit


def verify_draft_subtitles(audit, draft):
    """Read actual native text materials and intervals, never just track counts."""
    expected = [event for entry in (audit or {}).get('entries', [])
                if entry.get('status') == 'compiled' for event in entry.get('expected_events', [])]
    if not expected:
        return {'status': 'not_requested', 'verified_events': 0, 'aesthetic_verified': False}
    materials = {}
    for item in (draft.get('materials') or {}).get('texts', []):
        try:
            materials[item['id']] = json.loads(item['content'])['text']
        except (TypeError, KeyError, json.JSONDecodeError):
            continue
    actual = []
    for track in draft.get('tracks', []):
        if track.get('name') != 'subtitle':
            continue
        for segment in track.get('segments', []):
            tr = segment.get('target_timerange') or {}
            actual.append({'text': materials.get(segment.get('material_id')),
                'start': tr.get('start', -1) / 1e6,
                'end': (tr.get('start', -1) + tr.get('duration', 0)) / 1e6})
    used, missing = set(), []
    for event in expected:
        found = next((i for i, row in enumerate(actual) if i not in used
            and row['text'] == event['text'] and abs(row['start'] - event['start']) <= .002
            and abs(row['end'] - event['end']) <= .002), None)
        if found is None:
            missing.append(event)
        else:
            used.add(found)
    return {'status': 'complete' if not missing else 'needs_review',
            'expected_events': len(expected), 'verified_events': len(used),
            'missing_events': missing, 'aesthetic_verified': False,
            'scope': 'native draft text and timing readback; not editor playback or export'}
