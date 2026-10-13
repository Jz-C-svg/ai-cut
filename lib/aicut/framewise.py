"""Resumable local visual inference for every decoded source frame.

Unlike shot profiling, no similar frames are skipped. A limited diagnostic run
is always reported as incomplete. Each observation retains its source PTS.
"""
from __future__ import annotations
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from . import llm
from .paths import WORK, ffmpeg, ffprobe
from .media_integrity import audit_decode, resolve_original
from .production import atomic_json, file_lock

PROMPT = ('只描述当前图片可见事实，不推测台词、身份或画外物品。'
          '返回JSON：subject（主体）,action（当前可见动作，不猜运动过程）,'
          'objects（可见物品数组）,visible_text（确实可读的文字，无则空字符串）。')
OBSERVATION_SCHEMA = {'type': 'object', 'properties': {
    'subject': {'type': 'string', 'minLength': 1},
    'action': {'type': 'string'},
    'objects': {'type': 'array', 'items': {'type': 'string'}},
    'visible_text': {'type': 'string'}},
    'required': ['subject', 'action', 'objects', 'visible_text'], 'additionalProperties': False}


def scan(video: str | Path, *, limit: int | None = None, progress=print,
         original: bool = True) -> dict:
    requested = str(Path(video).resolve())
    video = resolve_original(video) if original else Path(video).resolve(strict=True)
    lock_id = hashlib.sha256(str(Path(video).resolve()).encode()).hexdigest()
    with file_lock(WORK / 'locks' / f'framewise-{lock_id}.lock', timeout=1):
        result = _scan(video, limit=limit, progress=progress)
        result['requested_video'] = requested
        result['input_policy'] = 'manifest_original' if original else 'explicit_current_file'
        atomic_json(WORK / 'framewise' / result['identity'] / 'status.json', result)
        return result


def valid_observation(observed):
    return (isinstance(observed, dict) and isinstance(observed.get('subject'), str)
            and bool(observed['subject'].strip()) and isinstance(observed.get('action'), str)
            and isinstance(observed.get('objects'), list)
            and all(isinstance(item, str) for item in observed['objects'])
            and isinstance(observed.get('visible_text'), str))


def summarize_for_edit(status_path: str | Path, *, max_events: int = 80) -> list[dict]:
    """Load only a complete scan and compress repeated per-frame facts into a timeline.

    The image model still examines every decoded frame. This compact, provenance-
    retaining timeline is the form passed into the editing decision context.
    """
    status_path = Path(status_path).resolve(strict=True)
    status = json.loads(status_path.read_text(encoding='utf-8'))
    expected = int(status.get('expected_frames', -1))
    observed = int(status.get('observed_frames', -1))
    if status.get('state') != 'complete' or not status.get('complete') or expected < 1 or observed != expected:
        raise ValueError('逐帧画面理解尚未完整，不能用于剪辑决策')
    records_dir = Path(status.get('records', '')).resolve(strict=True)
    rows = []
    previous_timestamp = -1.0
    for index in range(expected):
        path = records_dir / f'{index:08d}.json'
        if not path.is_file():
            raise ValueError(f'逐帧记录缺失: {index}')
        row = json.loads(path.read_text(encoding='utf-8'))
        obs = row.get('observation')
        if row.get('status') != 'observed' or not valid_observation(obs):
            raise ValueError(f'逐帧记录无效: {index}')
        try:
            timestamp = float(row['source_pts'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'逐帧时间码无效: {index}') from exc
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError(f'逐帧时间码无效: {index}')
        if timestamp < previous_timestamp:
            raise ValueError(f'逐帧时间码倒序: {index}')
        previous_timestamp = timestamp
        signature = (obs['subject'].strip(), obs['action'].strip(),
                     tuple(x.strip() for x in obs['objects'] if x.strip()), obs['visible_text'].strip())
        if rows and rows[-1]['signature'] == signature:
            rows[-1]['end'] = timestamp
            rows[-1]['frame_count'] += 1
        else:
            rows.append({'start': timestamp, 'end': timestamp, 'frame_count': 1,
                         'signature': signature})
    events = []
    for row in rows:
        subject, action, objects, visible_text = row['signature']
        events.append({'start': round(row['start'], 3), 'end': round(row['end'], 3),
                       'frame_count': row['frame_count'], 'subject': subject, 'action': action,
                       'objects': list(objects), 'visible_text': visible_text,
                       'provenance': 'automatic_visual_observation_unreviewed'})
    if len(events) > max_events:
        stride = (len(events) - 1) / (max_events - 1)
        events = [events[round(i * stride)] for i in range(max_events)]
    return events


def _scan(video: str | Path, *, limit: int | None = None, progress=print) -> dict:
    video = Path(video).resolve(strict=True)
    if limit is not None and limit < 1:
        raise ValueError('limit must be positive')
    integrity = audit_decode(video)
    if not integrity['complete']:
        raise RuntimeError('Full decode failed before frame inference')
    model = llm.get_vision_model()
    info = llm._post(llm.ollama_url('/api/show'), {'model': model}, timeout=10)
    digest = hashlib.sha256()
    with video.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    # Bind resumable observations to the full inference/extraction contract. A
    # change in sampling scale or FFmpeg build must not reuse stale frames.
    try:
        ffmpeg_version = subprocess.run([ffmpeg(), '-version'], capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=10, check=True).stdout.splitlines()[0]
    except (IndexError, OSError, subprocess.SubprocessError):
        ffmpeg_version = 'unknown'
    extraction_policy = f'960:-2;fps_mode=passthrough;q:v=3;{ffmpeg_version}:v2'
    identity = hashlib.sha256((digest.hexdigest() + str(info.get('modelfile')) + PROMPT
        + json.dumps(OBSERVATION_SCHEMA, sort_keys=True) + extraction_policy).encode()).hexdigest()
    directory = WORK / 'framewise' / identity
    directory.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run([ffprobe(), '-v', 'error', '-select_streams', 'v:0',
        '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(video)],
        capture_output=True, text=True, encoding='utf-8', check=True)
    stamps = [float(row['best_effort_timestamp_time']) for row in json.loads(probe.stdout)['frames']]
    if len(stamps) != integrity['decoded_video_frames']:
        raise RuntimeError('Frame count differs between decode and timestamp probe')
    images = directory / 'images'
    images.mkdir(exist_ok=True)
    extraction_marker = directory / 'extraction-complete.json'
    extracted = sorted(images.glob('*.jpg'))
    extraction_valid = False
    if extraction_marker.is_file():
        try:
            marker = json.loads(extraction_marker.read_text(encoding='utf-8'))
            expected_names = [f'{i:08d}.jpg' for i in range(1, len(stamps) + 1)]
            extraction_valid = (marker.get('frames') == len(stamps)
                                and [p.name for p in extracted] == expected_names
                                and all(p.stat().st_size > 0 for p in extracted))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            extraction_valid = False
    if not extraction_valid:
        # Images are reproducible intermediates. Rebuild the set atomically at
        # the directory level so a stale/missing frame cannot block forever.
        shutil.rmtree(images)
        images.mkdir(parents=True, exist_ok=True)
        extraction_marker.unlink(missing_ok=True)
        subprocess.run([ffmpeg(), '-hide_banner', '-nostdin', '-v', 'error', '-xerror', '-y',
            '-i', str(video), '-map', '0:v:0', '-vf', 'scale=960:-2', '-fps_mode', 'passthrough',
            '-q:v', '3', str(images / '%08d.jpg')], capture_output=True, check=True)
        if len(list(images.glob('*.jpg'))) != len(stamps):
            raise RuntimeError('Extracted image count differs from decoded frame count')
        atomic_json(extraction_marker, {'frames': len(stamps), 'policy': extraction_policy})
    records = directory / 'observations'
    records.mkdir(exist_ok=True)
    done = 0
    def save_status(state='running', error=None):
        result = {'video': str(video), 'model': model, 'identity': identity,
            'expected_frames': len(stamps), 'observed_frames': done,
            'complete': done == len(stamps), 'semantic_verified': False,
            'state': 'complete' if done == len(stamps) else state,
            'error': error,
            'records': str(records), 'integrity': integrity}
        atomic_json(directory / 'status.json', result)
        return result
    save_status()
    for index, timestamp in enumerate(stamps):
        record_path = records / f'{index:08d}.json'
        frame = images / f'{index + 1:08d}.jpg'
        frame_hash = hashlib.sha256(frame.read_bytes()).hexdigest()
        if record_path.exists():
            cached = json.loads(record_path.read_text(encoding='utf-8'))
            if (cached.get('image_sha256') == frame_hash and cached.get('status') == 'observed'
                    and valid_observation(cached.get('observation'))):
                done += 1
                save_status()
                continue
        if limit is not None and done >= limit:
            break
        attempts = []
        failure = None
        for attempt in range(3):
            try:
                observed = llm.vision_json(frame, PROMPT, provider='ollama', model=model,
                                          timeout=900, schema=OBSERVATION_SCHEMA)
                if not valid_observation(observed):
                    attempts.append({'attempt': attempt + 1, 'returned': observed,
                                     'error': 'incomplete_or_invalid_visual_fields'})
                    raise RuntimeError(f'Frame {index} has incomplete or invalid visual fields')
                failure = None
                break
            except Exception as exc:
                failure = exc
                if len(attempts) <= attempt:
                    attempts.append({'attempt': attempt + 1, 'error': type(exc).__name__,
                                     'message': str(exc)[:300]})
        if attempts:
            atomic_json(records / f'{index:08d}.attempts.json', {'frame_index': index,
                'source_pts': timestamp, 'attempts': attempts})
        if failure is not None:
            save_status('failed', {'frame_index': index, 'source_pts': timestamp,
                                  'type': type(failure).__name__, 'message': str(failure)[:300]})
            raise failure
        atomic_json(record_path, {'frame_index': index, 'source_pts': timestamp,
            'image': str(frame), 'image_sha256': frame_hash, 'observation': observed,
            'status': 'observed', 'provenance': 'automatic_visual_observation_unreviewed'})
        done += 1
        save_status()
        progress(f'[逐帧理解] {done}/{len(stamps)} PTS={timestamp:.3f}s', flush=True)
    return save_status('diagnostic_incomplete')
