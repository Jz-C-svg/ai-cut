"""Build a provenance-preserving local study bundle for short-video copywriting.

This is a retrieval/study corpus, not a model fine-tuning dataset. Existing
synthetic editing samples are compacted and explicitly marked unreviewed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '08-data/creative-writing-learning'
BUNDLE = OUT / 'learning_bundle.jsonl'
STUDY = OUT / 'hermes_study_input.jsonl'
MANIFEST = OUT / 'manifest.json'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines()
            if line.strip()]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    output: list[dict] = []
    source_counts: dict[str, int] = {}
    source_hashes: dict[str, str] = {}
    seen: set[str] = set()

    def add(source: Path, locator: str, kind: str, title: str, content: Any,
            evidence: str, rights: str, extra: dict | None = None) -> None:
        source_rel = source.relative_to(ROOT).as_posix()
        payload = json.dumps(content, ensure_ascii=False, sort_keys=True)
        fingerprint = sha((kind + '\n' + payload).encode('utf-8'))
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        source_hashes[source_rel] = sha(source.read_bytes())
        record = {
            'id': f'cw-{fingerprint[:16]}', 'kind': kind, 'title': title,
            'content': content, 'source_path': source_rel, 'source_locator': locator,
            'source_sha256': source_hashes[source_rel], 'record_sha256': fingerprint,
            'evidence_status': evidence, 'rights_status': rights,
        }
        if extra:
            record.update(extra)
        output.append(record)
        source_counts[source_rel] = source_counts.get(source_rel, 0) + 1

    rag = ROOT / 'content-library/rag'
    for name, evidence, rights in [
        ('viral_short_video_rag.jsonl', 'curated_reference_source_quality_varies', 'local knowledge summaries'),
        ('platform_knowledge.jsonl', 'source_summaries_keep_official_status_and_limits', 'facts_and_methods_only'),
        ('professional_knowledge.jsonl', 'curated_professional_reference', 'local reference; upstream terms retained'),
    ]:
        path = rag / name
        for i, row in enumerate(rows(path), 1):
            title = str(row.get('title') or row.get('name') or row.get('id') or f'{name}:{i}')
            # Never treat the account-export preference record as public writing evidence.
            add(path, f'line:{i}:{row.get("id", title)}', 'rag_reference', title, row,
                evidence, rights)

    # Only operational cards about writing decisions, evidence, and review are relevant.
    core = rag / 'knowledge_core.jsonl'
    core_titles = {'后台数据复盘匹配', '学习库检索机制', '检索资料不是操作授权',
                   '质量门不可绕过', '任务状态与证据'}
    for i, row in enumerate(rows(core), 1):
        if row.get('title') in core_titles:
            add(core, f'line:{i}:{row.get("id", row.get("title"))}', 'operational_reference',
                row['title'], row, row.get('evidence_status', 'curated_from_local_sources'),
                'internal project knowledge; local use')

    content = ROOT / 'content-library'
    for name in ('structures.json', 'styles.json'):
        path = content / name
        value = read_json(path)
        add(path, '$', 'structure_catalog' if name.startswith('structures') else 'style_catalog',
            name, value, 'curated_local_catalog', 'local project reference')

    # Full style cards and architecture definitions are the primary reusable craft data.
    for folder, kind in [('styles', 'style_card'), ('architectures', 'story_architecture')]:
        for path in sorted((content / folder).glob('*.json')):
            value = read_json(path)
            add(path, '$', kind, str(value.get('名称') or value.get('name') or path.stem),
                value, 'project_authored_or_curated_definition', 'local project reference')

    # External-video-derived style analysis is retained as an unreviewed observation only.
    analyzed = content / 'styles-analyzed.json'
    for i, row in enumerate(read_json(analyzed).get('styles', []), 1):
        add(analyzed, f'$.styles[{i-1}]#{row.get("id", i)}', 'observed_style_candidate',
            str(row.get('名称') or row.get('id') or f'observation-{i}'), row,
            'assistant_visual_analysis_unreviewed', 'source-video rights and training permission unverified')

    # Official platform summaries relevant to storytelling and audience response.
    web_dir = content / 'web-learning'
    topics = {'short_form_storytelling', 'storytelling_performance', 'performance_analysis',
              'editing_rhythm_story'}
    for path in sorted(web_dir.glob('*.json')):
        value = read_json(path)
        if value.get('topic') not in topics:
            continue
        for item in value.get('items', []):
            license_status = str(item.get('license_status') or 'not_recorded')
            add(path, f'$.items#{item.get("id", item.get("title", "item"))}',
                'official_platform_method', str(item.get('title') or item.get('id')),
                item, 'public_source_summary_not_performance_guarantee', license_status,
                {'source_url': item.get('source_url'), 'accessed_date': item.get('accessed_date')})

    # Keep all 63 existing samples, but extract decision structure and flag their
    # automatically generated, unreviewed labels instead of treating text as gold.
    samples_path = ROOT / 'data/finetune_samples/finetune_samples.jsonl'
    for i, row in enumerate(rows(samples_path), 1):
        out = row.get('output') or {}
        decision = out.get('architecture_decision') or {}
        plan = decision.get('段落计划') or []
        compact = {
            'instruction': row.get('instruction', ''),
            'input': row.get('input', ''),
            'matched_arch': decision.get('matched_arch'),
            'architecture_name': decision.get('架构名称'),
            'matching_reason': decision.get('匹配理由', ''),
            'beat_functions': [p.get('段落功能') for p in plan if isinstance(p, dict)],
            'hook_pattern': str(out.get('钩子设计', ''))[:280],
            'cta_pattern': str(out.get('CTA设计', ''))[:220],
        }
        add(samples_path, f'line:{i}', 'synthetic_editing_example_not_copy_gold',
            f'生成候选样本 {i:02d}', compact,
            'automatically_generated_unreviewed_candidate',
            'local account-derived context; external training rights not verified')

    # Include the full reusable writing and editing-decision libraries. These
    # are kept separate from the platform corpus and from Video-RAG records.
    for filename in ('hooks.json', 'platform_style_router.json', 'index.json',
                     '评估标准.md', 'templates/koubo-zhishi-huangdan-v1.json'):
        path = ROOT / 'content-library' / filename
        if path.is_file():
            value = (json.loads(path.read_text(encoding='utf-8-sig'))
                     if path.suffix.lower() == '.json' else path.read_text(encoding='utf-8-sig'))
            add(path, '$', 'writing_reference', path.stem, value,
                'local_project_reference_requires_task_fit', 'local project reference')

    # Knowledge-pack portions with direct writing/decision relevance only.
    kp_path = content / 'knowledge_pack.json'
    kp = read_json(kp_path)
    for key in ('shortform', 'decision_methodology'):
        if key in kp:
            add(kp_path, f'$.{key}', 'shortform_method', key, kp[key],
                'legacy_project_knowledge_requires_case_validation', 'local project reference')

    # Deduplicate exact normalized content across inputs while retaining the first source.
    BUNDLE.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in output),
                      encoding='utf-8')
    # Keep one traceable excerpt for every corpus record so the local model can
    # study the whole source set inside a practical context budget.
    study_rows = []
    for record in output:
        content = record.get('content')
        if record['kind'] == 'synthetic_editing_example_not_copy_gold' and isinstance(content, dict):
            excerpt = {
                'input': str(content.get('input', ''))[:95],
                'matched_arch': content.get('matched_arch'),
                'architecture_name': content.get('architecture_name'),
                'beat_functions': content.get('beat_functions', [])[:4],
                'hook_pattern': str(content.get('hook_pattern', ''))[:65],
                'cta_pattern': str(content.get('cta_pattern', ''))[:45],
            }
        elif isinstance(content, dict):
            # Drop hashes/paths already carried at record level and preserve the
            # most useful craft/evidence fields across heterogeneous source schemas.
            priority = ('名称', 'name', 'title', '描述', 'description', '适用', '目标', 'audience',
                        '结构', 'steps', '段落', 'hook', '钩子', 'CTA', 'cta', '转折', '冲突',
                        '情绪', '节奏', 'dialogue', 'beats', '适合', '限制', 'limitations',
                        'summary', 'text', 'why', 'evidence_status', 'rights_status')
            excerpt = {}
            for key in priority:
                if key in content:
                    value = content[key]
                    serialized = json.dumps(value, ensure_ascii=False)
                    excerpt[key] = serialized[:90] + ('…' if len(serialized) > 90 else '')
                    if len(json.dumps(excerpt, ensure_ascii=False)) >= 245:
                        break
            if not excerpt:
                excerpt = json.dumps(content, ensure_ascii=False)[:220]
        else:
            excerpt = str(content)[:220]
        excerpt_text = json.dumps(excerpt, ensure_ascii=False)
        if len(excerpt_text) > 255:
            excerpt_text = excerpt_text[:250] + '…'
        study_rows.append({
            'ref': record['id'], 'kind': record['kind'], 'title': record['title'][:70],
            'source': Path(record['source_path']).name,
            'evidence': record['evidence_status'][:60], 'rights': record['rights_status'][:60],
            'excerpt': excerpt_text,
        })
    STUDY.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in study_rows),
                     encoding='utf-8')
    manifest = {
        'name': 'short_video_copywriting_learning_bundle',
        'version': '1.0', 'created_from': 'local project datasets',
        'record_count': len(output), 'source_count': len(source_counts),
        'source_counts': source_counts, 'source_sha256': source_hashes,
        'bundle_sha256': sha(BUNDLE.read_bytes()), 'bundle_bytes': BUNDLE.stat().st_size,
        'hermes_study_input_sha256': sha(STUDY.read_bytes()),
        'hermes_study_input_bytes': STUDY.stat().st_size,
        'evidence_policy': {
            'synthetic_examples': 'retained as low-confidence decision candidates; not human gold labels',
            'observed_styles': 'unreviewed observations; not exact style/asset replication instructions',
            'public_sources': 'methods and facts only; no source media copied or permission inferred',
            'account_derived_data': 'local-only; external training permission remains unverified',
            'model_training': 'no weights trained; bundle prepared for local Hermes study and RAG integration',
        },
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'records': len(output), 'sources': len(source_counts),
                      'bytes': BUNDLE.stat().st_size, 'study_bytes': STUDY.stat().st_size,
                      'bundle_sha256': manifest['bundle_sha256'],
                      'source_counts': source_counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
