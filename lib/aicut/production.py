"""Recoverable file commits and objective production checks (no model calls)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import time
import uuid
from pathlib import Path


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def project_name(value):
    if not isinstance(value, str) or not value or len(value) > 120:
        raise ValueError('工程名必须为 1–120 字符')
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith((' ', '.')) or value in ('.', '..'):
        raise ValueError('工程名不能包含路径或 Windows 非法字符')
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))}:
        raise ValueError('工程名不能使用 Windows 保留名称')
    return value


@contextlib.contextmanager
def file_lock(path, timeout=30):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0, 2)
        if not f.tell():
            f.write(b'0'); f.flush()
        until = time.monotonic() + timeout
        while True:
            try:
                f.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= until:
                    raise TimeoutError(f'任务正在使用该资源: {path}')
                time.sleep(.1)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def source_version(path, render):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f'不是素材文件: {path}')
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError('素材读取期间被修改，请重试')
    asset = hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()[:12]
    version = hashlib.sha256((digest.hexdigest() + json.dumps(render, sort_keys=True)).encode()).hexdigest()[:16]
    return {'asset_id': asset, 'version': version, 'sha256': digest.hexdigest(),
            'size': after.st_size, 'mtime_ns': after.st_mtime_ns}


def validate_draft(path):
    data = read_json(Path(path) / 'draft_content.json')
    duration = data.get('duration', 0)
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('草稿总时长无效')
    videos = []
    for track in data.get('tracks', []):
        for seg in track.get('segments', []):
            tr = seg.get('target_timerange') or {}
            start, length = tr.get('start', -1), tr.get('duration', 0)
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (start, length)) or start < 0 or length <= 0:
                raise ValueError('草稿存在非法片段时间')
            if start + length > duration + 100000:
                raise ValueError('草稿片段超出总时长')
            if track.get('type') == 'video':
                videos.append(seg)
    if not videos:
        raise ValueError('草稿没有视频片段')
    video_tracks = [t for t in data['tracks'] if t.get('type') == 'video' and t.get('segments')]
    main = next((t for t in video_tracks if t.get('name') == 'main'), video_tracks[0])
    end = 0
    for seg in sorted(main['segments'], key=lambda s: s['target_timerange']['start']):
        tr = seg['target_timerange']
        if abs(tr['start'] - end) > 100000:
            raise ValueError('主画面时间线存在空档或重叠')
        end = tr['start'] + tr['duration']
    if abs(end - duration) > 100000:
        raise ValueError('主画面与草稿总时长不一致')
    for kind in ('videos', 'audios'):
        for material in (data.get('materials') or {}).get(kind, []):
            raw = material.get('path')
            if raw and not Path(raw).is_file():
                raise ValueError(f'草稿素材不可访问: {raw}')
    return {'passed': True, 'duration': duration / 1e6, 'video_segments': len(videos)}


def commit_draft(source, target, before_replace=None):
    """Stage on destination volume; retain previous generation, journal and rollback.

    An interrupted two-rename commit is recovered on the next attempt. Never
    delete a pre-existing draft. A locked/open editor draft must fail visibly.
    """
    source, target = Path(source).resolve(), Path(target)
    if target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction()):
        raise ValueError('目标草稿不能是符号链接或目录联接')
    target = target.resolve()
    project_name(target.name)
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('源草稿与目标草稿必须独立')
    validate_draft(source)
    root = target.parent
    root.mkdir(parents=True, exist_ok=True)
    control = root / '.aicut-transactions' / target.name
    control.mkdir(parents=True, exist_ok=True)
    with file_lock(control / 'lock'):
        journal = control / 'pending.json'
        if journal.exists():
            previous = read_json(journal)
            backup = Path(previous['backup']).resolve()
            if backup.parent != control.resolve():
                raise ValueError('无效的恢复记录')
            if not target.exists() and backup.exists():
                backup.rename(target)
            journal.unlink()
        token = uuid.uuid4().hex
        staging, backup = control / (token + '.staged'), control / (token + '.backup')
        shutil.copytree(source, staging)
        validate_draft(staging)
        if target.exists() and before_replace:
            before_replace(target)
        atomic_json(journal, {'target': str(target), 'backup': str(backup), 'staging': str(staging)})
        try:
            if target.exists():
                target.rename(backup)
            staging.rename(target)
        except BaseException:
            if not target.exists() and backup.exists():
                backup.rename(target)
            raise
        else:
            journal.unlink()
        return {'target': str(target), 'backup': str(backup) if backup.exists() else None}
