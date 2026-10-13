"""Check source-grounded editorial claims independently of model confidence."""
import re

NUMBERS = {word: index for index, word in enumerate('一二三四五六七八九十', 1)}


def _ordinal(text):
    match = re.search(r'第([一二三四五六七八九十]|\d+)', text)
    if not match:
        return None
    value = match[1]
    return int(value) if value.isdigit() else NUMBERS[value]


def review_intent(intent, segments):
    issues = []
    for row in intent.get('逐句意图') or []:
        number = row.get('句号')
        if not isinstance(number, int) or not 1 <= number <= len(segments):
            issues.append({'kind': 'invalid_sentence_reference', 'sentence': number})
            continue
        source = str(segments[number - 1].get('text', ''))
        source_ordinal, proposed_ordinal = _ordinal(source), _ordinal(str(row.get('理由', '')))
        if source_ordinal and proposed_ordinal and source_ordinal != proposed_ordinal:
            issues.append({'kind': 'ordinal_mismatch', 'sentence': number,
                'source': source_ordinal, 'proposed': proposed_ordinal,
                'reason': '方案编号与原句编号不一致，需修正而不是按模型置信度放行'})
    architecture = intent.get('architecture_decision') or {}
    cta = str(architecture.get('CTA设计', ''))
    source_text = ''.join(str(segment.get('text', '')) for segment in segments)
    for clause in re.split(r'[，。；;\n]', cta):
        if '口播' not in clause or re.search(r'不要|禁止|不添加|不新增', clause):
            continue
        for phrase in re.findall(r'[“「‘](.*?)[”」’]', clause):
            if phrase and phrase not in source_text:
                issues.append({'kind': 'unavailable_spoken_cta', 'text': phrase,
                    'reason': '原始语音没有这句口播；需要明确补录或改为纯文字方案'})
    return {'status': 'needs_revision' if issues else 'candidate_for_confirmation',
            'blocking': bool(issues), 'issues': issues,
            'scope': 'source numbering and proposed spoken CTA; not complete creative quality validation'}


def review_visual_decisions(intent, segments, visual_profiles=None):
    """Audit whether retrieved visual rules became grounded, reviewable decisions.

    This is a conservative QA report, not a visual-quality score or render claim.
    """
    profiles = visual_profiles or []
    evidence = {str(row.get('id')): row for row in
                ((intent.get('learning_context') or {}).get('evidence') or [])
                if isinstance(row, dict) and row.get('id')}
    cards = {key: row for key, row in evidence.items()
             if str(row.get('id', '')).startswith('vdc-')}
    rows = {int(row.get('句号')): row for row in (intent.get('逐句意图') or [])
            if isinstance(row, dict) and str(row.get('句号', '')).isdigit()}
    applied = (intent.get('learning_application') or {}).get('采用') or []
    application_sentences = {int(n) for row in applied if isinstance(row, dict)
                             for n in (row.get('对应句号') or [])
                             if str(n).isdigit()}
    applied_ids = {str(row.get('证据id')) for row in applied if isinstance(row, dict)}
    sentence_audit = []
    for n, row in sorted(rows.items()):
        refs = [str(ref) for ref in (row.get('知识依据') or [])]
        card_refs = [ref for ref in refs if ref in cards]
        if not card_refs:
            continue
        source = str(segments[n - 1].get('text', '')) if 1 <= n <= len(segments) else ''
        has_visual_proof = bool(profiles) and any(
            (p.get('semantic_evidence') or {}).get('status') in {
                'automatic_visual_observation_unreviewed', 'human_reviewed_visual_observation'}
            and any(k in str(p) for k in ('t0', 't1', 'shot_id')) for p in profiles)
        sentence_audit.append({
            'sentence': n, 'source_text': source[:100], 'card_ids': card_refs,
            'application_claimed': any(ref in applied_ids and n in application_sentences for ref in card_refs),
            'visual_profile_available': has_visual_proof,
            'status': 'needs_visual_review' if not has_visual_proof else 'grounded_candidate',
            'checks': ['核对决策是否对应本句台词', '核对镜头时间码与画面证据',
                       '确认字幕/贴图未遮挡主体']
        })
    return {
        'version': 'visual-decision-audit-v1',
        'status': 'needs_review' if any(r['status'] == 'needs_visual_review' for r in sentence_audit)
                  else 'candidate_for_confirmation',
        'knowledge_cards_retrieved': sorted(cards),
        'knowledge_cards_applied': sorted(applied_ids & set(cards)),
        'sentences': sentence_audit,
        'unapplied_cards': sorted(set(cards) - applied_ids),
        'limits': '依据引用与结构字段做静态审查；不证明画面美观、特效已渲染或整片已逐帧人工复核。'
    }
