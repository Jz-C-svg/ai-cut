"""Persisted stage state and cancellable subprocess execution."""
from __future__ import annotations
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from .production import atomic_json, project_name, read_json


def mark_interrupted(root, task_id, state, reason):
    """Called only after the coordinator process has exited/been stopped."""
    path = Path(root) / 'work' / 'tasks' / project_name(task_id) / 'status.json'
    if not path.is_file():
        return
    data = read_json(path)
    if data.get('state') != 'running':
        return
    now = time.time()
    data.update(state=state, error=reason, finished_at=now, updated_at=now)
    for stage in data.get('stages', []):
        if stage.get('state') == 'running':
            stage.update(state=state, error=reason, finished_at=now)
    atomic_json(path, data)


class Cancelled(RuntimeError):
    pass


def stop_process(proc):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                       capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=15)


def run_command(cmd, *, cwd, env=None, timeout=3600, cancelled=lambda: False, on_line=print):
    if cancelled():
        raise Cancelled('任务已取消')
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                            start_new_session=os.name != 'nt')
    lines = queue.Queue()
    def read():
        try:
            for line in proc.stdout:
                lines.put(line.rstrip())
        finally:
            lines.put(None)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    start = time.monotonic()
    closed = False
    try:
        while not closed or proc.poll() is None:
            if cancelled():
                raise Cancelled('任务已取消')
            if time.monotonic() - start > timeout:
                raise TimeoutError(f'阶段超时（{timeout} 秒）')
            try:
                line = lines.get(timeout=.1)
                if line is None:
                    closed = True
                elif line:
                    on_line(line)
            except queue.Empty:
                pass
        rc = proc.wait()
        if rc:
            raise subprocess.CalledProcessError(rc, cmd)
        return rc
    finally:
        stop_process(proc)
        reader.join(timeout=2)
        proc.stdout.close()


class Job:
    def __init__(self, root, task_id, timeout=3600):
        project_name(task_id)
        self.directory = Path(root) / 'work' / 'tasks' / task_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.path = self.directory / 'status.json'
        self.stop = self.directory / 'cancel'
        self.timeout = timeout
        self.data = {'id': task_id, 'state': 'running', 'created_at': time.time(),
                     'stages': [], 'artifacts': {}, 'warnings': []}
        self.save()

    def save(self):
        self.data['updated_at'] = time.time()
        atomic_json(self.path, self.data)

    def check_cancel(self):
        if self.stop.exists():
            raise Cancelled('任务已取消')

    def stage(self, name, action):
        self.check_cancel()
        stage = {'name': name, 'state': 'running', 'started_at': time.time()}
        self.data['stages'].append(stage)
        self.save()
        print(f'[阶段] {name}', flush=True)
        try:
            result = action()
            self.check_cancel()
            stage['state'] = 'succeeded'
            return result
        except BaseException as exc:
            stage.update(state='cancelled' if isinstance(exc, Cancelled) else 'failed', error=str(exc))
            raise
        finally:
            stage['finished_at'] = time.time()
            self.save()

    def command(self, cmd, root):
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
                   PYTHONPATH=str(Path(root) / 'lib'))
        with (self.directory / 'process.log').open('a', encoding='utf-8') as log:
            def emit(line):
                log.write(line + '\n'); log.flush()
                print(line, flush=True)
            return run_command(cmd, cwd=root, env=env, timeout=self.timeout,
                               cancelled=self.stop.exists, on_line=emit)

    def finish(self, state, error=None):
        self.data.update(state=state, finished_at=time.time())
        if error:
            self.data['error'] = error
        self.save()
        print(f'[任务 {state}] {self.path}', flush=True)
