#!/usr/bin/env python3
r"""AI cut 主机服务：把内容库、合规自检、评估体系与剪辑流水线做成网络应用。

零依赖（仅 Python 标准库）。默认只监听本机，加 --host 0.0.0.0 后局域网可访问。

启动:
    python server.py                 # 本机 http://127.0.0.1:8848
    python server.py --host 0.0.0.0  # 局域网可访问（需要令牌）
"""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import json
import ipaddress
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
PROJECT = APP_DIR.parent
WEB_DIR = APP_DIR / "web"
PY = Path(sys.executable)
CONFIG_FILE = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8848,
    "token": "aicut",
    "allowed_roots": [str(PROJECT)],
    "ffmpeg": "ffmpeg",
}

TASKS: dict[str, dict] = {}
TASKS_LOCK = threading.Lock()
VIDEO_BRAINS: dict[str, object] = {}
VIDEO_BRAINS_LOCK = threading.RLock()


def load_config() -> dict:
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    else:
        cfg = dict(DEFAULT_CONFIG)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg.setdefault("host", DEFAULT_CONFIG["host"])
    cfg.setdefault("port", DEFAULT_CONFIG["port"])
    cfg.setdefault("token", DEFAULT_CONFIG["token"])
    cfg.setdefault("allowed_roots", DEFAULT_CONFIG["allowed_roots"])
    return cfg


def prepare_lan_token(config: dict, config_path: Path | None = None) -> tuple[dict, bool]:
    """Replace the known demo token before exposing the service to a LAN."""
    result = dict(config)
    token = result.get('token')
    if isinstance(token, str) and len(token) >= 32 and token != DEFAULT_CONFIG['token']:
        return result, False
    result['token'] = secrets.token_urlsafe(32)
    path = Path(config_path or CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)
    return result, True


def is_lan_bind(host: str) -> bool:
    value = (host or '').strip().strip('[]').lower()
    if value in {'', 'localhost'}:
        return False
    try:
        return not ipaddress.ip_address(value).is_loopback
    except ValueError:
        # Any non-loopback hostname is conservatively treated as LAN exposed.
        return True


CONFIG = load_config()


def safe_path(raw: str) -> Path:
    """只允许访问白名单目录内的文件，防止目录穿越。"""
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT / p
    resolved = p.resolve()
    for root in CONFIG["allowed_roots"]:
        try:
            resolved.relative_to(Path(root).resolve())
            return resolved
        except ValueError:
            continue
    raise PermissionError(f"路径不在允许范围内: {raw}")


def load_library(kind: str) -> dict:
    files = {'styles': 'styles.json', 'hooks': 'hooks.json',
             'structures': 'structures.json', 'topics': 'topics.json'}
    directory = PROJECT / 'content-library'
    target = directory / files.get(kind, 'styles.json')
    data = json.loads(target.read_text(encoding='utf-8-sig'))
    if kind != 'styles':
        return data
    cards, warnings = [], []
    for entry in data.get('styles') or []:
        try:
            card = dict(entry)
            if card.get('文件'):
                source = (directory / card['文件']).resolve()
                source.relative_to(directory.resolve())
                detail = json.loads(source.read_text(encoding='utf-8-sig'))
                if detail.get('id') != card.get('id'):
                    raise ValueError('风格索引与详情 id 不一致')
                card.update(detail)
            if not isinstance(card.get('剪辑参数'), dict):
                raise ValueError('缺少剪辑参数')
            cards.append(card)
        except (OSError, ValueError, TypeError) as exc:
            warnings.append(f"{entry.get('id', 'unknown')}: {exc}")
    return {**data, 'styles': cards, 'warnings': warnings}


def run_json(script: str, args: list[str], timeout: int = 600) -> tuple[int, str]:
    cmd = [str(PY), str(PROJECT / "scripts" / script)] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=str(PROJECT))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def start_task(name: str, cmd: list[str], task_id: str | None = None) -> str:
    tid = task_id or uuid.uuid4().hex[:12]
    with TASKS_LOCK:
        TASKS[tid] = {"id": tid, "name": name, "status": "running", "log": [], "started": time.time()}

    def worker():
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["HF_ENDPOINT"] = "https://hf-mirror.com"
        env["HF_HUB_DISABLE_XET"] = "1"
        try:
            sys.path.insert(0, str(PROJECT / 'lib'))
            from aicut.jobs import run_command
            def emit(line):
                with TASKS_LOCK:
                    TASKS[tid]['log'].append(line)
            run_command(cmd, cwd=str(PROJECT), env=env, timeout=14400, on_line=emit)
            with TASKS_LOCK:
                TASKS[tid]["status"] = 'done'
                TASKS[tid]["code"] = 0
        except Exception as exc:  # noqa: BLE001
            from aicut.jobs import mark_interrupted
            code = getattr(exc, 'returncode', 1)
            state = 'needs_review' if code == 4 else 'cancelled' if code == 130 else 'failed'
            mark_interrupted(PROJECT, tid, state, str(exc))
            with TASKS_LOCK:
                TASKS[tid]["status"] = state
                TASKS[tid]['code'] = code
                TASKS[tid]["log"].append(f"异常: {exc}")
        finally:
            with TASKS_LOCK:
                TASKS[tid]["finished"] = time.time()

    threading.Thread(target=worker, daemon=True).start()
    return tid


class Handler(BaseHTTPRequestHandler):
    server_version = "AIcut/1.0"

    def log_message(self, fmt, *args):  # 静音访问日志，保持控制台干净
        pass

    # ---------- 基础 ----------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _authorized(self, query: dict) -> bool:
        token = self.headers.get("X-Auth-Token") or (query.get("token", [""])[0])
        return token == CONFIG["token"]

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"text": raw}

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path
        if path.startswith("/api/"):
            if not self._authorized(query):
                return self._json({"error": "令牌无效"}, 401)
            return self.api_get(path, query)
        return self.static(path)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            return self._json({"error": "令牌无效"}, 401)
        return self.api_post(parsed.path, self._body())

    # ---------- 静态文件 ----------
    def static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._send(403, b"forbidden", "text/plain")
        if not target.exists() or target.is_dir():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        if target.name == "index.html":
            stamp = str(int((WEB_DIR / "app.js").stat().st_mtime)) if (WEB_DIR / "app.js").exists() else "0"
            data = data.replace(b'src="app.js"', ('src="app.js?v=%s"' % stamp).encode())
            delivery_stamp = str(int((WEB_DIR / 'delivery.js').stat().st_mtime))
            data = data.replace(b'src="delivery.js"', ('src="delivery.js?v=%s"' % delivery_stamp).encode())
        return self._send(200, data, ctype)


    def serve_media(self, target: Path):
        """流式返回媒体文件，支持 Range，便于浏览器播放与拖动。"""
        size = target.stat().st_size
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            spec = rng.split("=", 1)[1].split(",")[0].strip()
            try:
                a, _, b = spec.partition("-")
                if a:
                    start = int(a)
                if b:
                    end = int(b)
                if not a and b:
                    start = max(0, size - int(b))
                    end = size - 1
                status = 206
            except ValueError:
                start, end = 0, size - 1
        start = max(0, min(start, size - 1))
        end = max(start, min(end, size - 1))
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with target.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(262144, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_HEAD(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path
        if path == "/api/media":
            if not self._authorized(query):
                return self._json({"error": "令牌无效"}, 401)
            try:
                target = safe_path(query.get("path", [""])[0])
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            return self.serve_media(target)
        return self._send(404, b"not found", "text/plain")

    # ---------- GET API ----------
    def api_get(self, path: str, query: dict):
        if path == '/api/delivery-plan':
            try:
                sheet = safe_path(query.get('sheet', [''])[0])
                if not sheet.is_file():
                    return self._json({'error': '决策单不存在'}, 404)
                sys.path[:0] = [str(PROJECT / 'scripts'), str(PROJECT / 'lib')]
                from deliver_edit import inspect_plan
                return self._json(inspect_plan(sheet, query.get('software', ['jianying'])[0],
                                               query.get('engine', ['project'])[0]))
            except PermissionError as exc:
                return self._json({'error': str(exc)}, 403)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                return self._json({'error': str(exc)}, 400)
        if path == '/api/editors':
            if str(PROJECT / 'lib') not in sys.path:
                sys.path.insert(0, str(PROJECT / 'lib'))
            from aicut.editor_capabilities import capabilities
            return self._json({'editors': capabilities()})
        if path.startswith('/api/delivery/'):
            tid = path.rsplit('/', 1)[-1]
            if not tid or len(tid) > 64 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in tid):
                return self._json({'error': '无效交付 id'}, 400)
            report = PROJECT / 'work' / 'deliveries' / ('api-' + tid) / 'delivery.json'
            if report.is_file():
                try:
                    return self._json(json.loads(report.read_text(encoding='utf-8')))
                except (OSError, json.JSONDecodeError):
                    return self._json({'status': 'report_updating'}, 202)
            with TASKS_LOCK:
                task = TASKS.get(tid)
            if task:
                return self._json({'status': task['status'], 'task': tid}, 202)
            return self._json({'error': '交付任务不存在'}, 404)
        if path == "/api/health":
            return self._json({
                "ok": True,
                "project": str(PROJECT),
                "python": str(PY),
                "python_ok": PY.exists(),
                "skills": (Path(portable_path('user:.agents/skills')) / "duanshipin-hegui-jianji").exists(),
                "version": str(int((WEB_DIR / "app.js").stat().st_mtime)) if (WEB_DIR / "app.js").exists() else "0",
            })
        if path.startswith("/api/video-brain/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                return self._json({"error": "观察接口路径无效"}, 404)
            _, _, brain_id, action = parts
            if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in brain_id):
                return self._json({"error": "观察任务 id 无效"}, 400)
            with VIDEO_BRAINS_LOCK:
                brain = VIDEO_BRAINS.get(brain_id)
            if brain is None:
                return self._json({"error": "观察任务不存在或服务已重启"}, 404)
            if action == "status":
                return self._json({"id": brain_id, **brain.state()})
            if action == "events":
                try:
                    after = max(0, int(query.get("after", ["0"])[0]))
                except ValueError:
                    return self._json({"error": "after 必须为整数游标"}, 400)
                events = [e for e in brain.events if int(e.get("seq", 0)) > after]
                return self._json({"events": events[-200:], "cursor": brain._event_seq,
                                   "more": len(events) > 200})
            if action == "frame":
                frame = brain.latest_frame_file
                if not frame.is_file():
                    return self._json({"error": "尚未生成预览画面"}, 404)
                return self._send(200, frame.read_bytes(), "image/jpeg")
            return self._json({"error": "未知观察接口"}, 404)
        if path == "/api/library":
            kind = (query.get("kind", ["styles"])[0])
            try:
                return self._json(load_library(kind))
            except FileNotFoundError:
                return self._json({'error': '内容库文件不存在'}, 404)
            except (ValueError, TypeError) as exc:
                return self._json({'error': str(exc)}, 400)
        if path == "/api/files":
            kind = query.get("kind", ["sheets"])[0]
            mapping = {
                "sheets": PROJECT / "03-scripts",
                "transcripts": PROJECT / "02-transcript",
                "drafts": PROJECT / "06-drafts",
                "data": PROJECT / "08-data",
                "normalized": PROJECT / "work" / "normalized",
                "reports": PROJECT / "08-data",
                "output": PROJECT / "07-output",
                "work": PROJECT / "work",
            }
            folder = mapping.get(kind)
            if folder and query.get("sub"):
                candidate = (folder / query["sub"][0]).resolve()
                try:
                    candidate.relative_to(folder.resolve())
                    folder = candidate
                except ValueError:
                    return self._json({"error": "子目录非法"}, 403)
            if not folder or not folder.exists():
                return self._json({"items": []})
            items = []
            for f in sorted(folder.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.name.startswith("~"):
                    continue
                items.append({"name": f.name, "dir": f.is_dir(), "path": str(f),
                              "size": f.stat().st_size if f.is_file() else 0,
                              "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))})
            return self._json({"items": items[:200]})
        if path == "/api/media":
            raw = query.get("path", [""])[0]
            try:
                target = safe_path(raw)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            if not target.exists() or target.is_dir():
                return self._json({"error": "文件不存在"}, 404)
            return self.serve_media(target)
        if path == "/api/file":
            raw = query.get("path", [""])[0]
            try:
                target = safe_path(raw)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            if not target.exists() or target.is_dir():
                return self._json({"error": "文件不存在"}, 404)
            if target.stat().st_size > 2 * 1024 * 1024:
                return self._json({"error": "文件过大，超过 2MB"}, 413)
            return self._json({"path": str(target), "text": target.read_text(encoding="utf-8", errors="replace")})
        if path == "/api/tasks":
            with TASKS_LOCK:
                items = sorted(TASKS.values(), key=lambda t: t["started"], reverse=True)[:30]
                return self._json({"items": [{k: v for k, v in t.items() if k != "log"} for t in items]})
        if path.startswith('/api/production/'):
            tid = path.rsplit('/', 1)[-1]
            if not tid or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in tid):
                return self._json({'error': '无效任务 id'}, 400)
            report = PROJECT / 'work' / 'tasks' / tid / 'status.json'
            if not report.is_file():
                return self._json({'error': '任务不存在'}, 404)
            return self._json(json.loads(report.read_text(encoding='utf-8')))
        if path.startswith("/api/tasks/"):
            tid = path.rsplit("/", 1)[-1]
            with TASKS_LOCK:
                task = TASKS.get(tid)
            if not task:
                return self._json({"error": "任务不存在"}, 404)
            return self._json(task)
        if path == "/api/system":
            info = {"project": str(PROJECT)}
            try:
                out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=15)
                info["ollama"] = out.stdout.strip() or out.stderr.strip()
            except Exception as exc:  # noqa: BLE001
                info["ollama"] = f"不可用: {exc}"
            try:
                info["disk_free_gb"] = round(shutil.disk_usage(str(PROJECT)).free / 1024 ** 3, 1)
            except Exception:  # noqa: BLE001
                pass
            return self._json(info)
        return self._json({"error": "未知接口"}, 404)

    # ---------- POST API ----------
    def api_post(self, path: str, body: dict):
        if path == '/api/delivery':
            try:
                if not isinstance(body, dict) or not isinstance(body.get('sheet'), str):
                    return self._json({'error': '请提供决策单路径'}, 400)
                sheet_path = safe_path(body['sheet'])
                if not sheet_path.is_file():
                    return self._json({'error': '决策单不存在'}, 400)
                output = safe_path(body['out']) if body.get('out') else None
            except PermissionError as exc:
                return self._json({'error': str(exc)}, 403)
            except (TypeError, ValueError) as exc:
                return self._json({'error': str(exc)}, 400)
            software = str(body.get('software') or 'jianying')
            engine = str(body.get('engine') or 'project')
            if software not in {'jianying', 'capcut', 'premiere', 'davinci', 'finalcut', 'vegas', 'aftereffects'} or engine not in {'project', 'portable', 'native'}:
                return self._json({'error': '软件或交付方式无效'}, 400)
            try:
                sys.path[:0] = [str(PROJECT / 'scripts'), str(PROJECT / 'lib')]
                from deliver_edit import inspect_plan
                reviewed = inspect_plan(sheet_path, software, engine)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                return self._json({'error': str(exc)}, 400)
            if body.get('approve_plan_sha256') != reviewed['sheet_sha256']:
                return self._json({'error': '方案尚未确认或已修改，请重新查看并确认当前版本'}, 409)
            if not reviewed['ready']:
                return self._json({'error': '；'.join(reviewed['blockers']), 'blockers': reviewed['blockers']}, 422)
            tid = uuid.uuid4().hex[:12]
            report = PROJECT / 'work' / 'deliveries' / ('api-' + tid) / 'delivery.json'
            args = [str(PY), str(PROJECT / 'scripts' / 'deliver_edit.py'), '--sheet', str(sheet_path),
                    '--software', software, '--engine', engine, '--report-out', str(report)]
            if output:
                args += ['--out', str(output)]
            if body.get('approve_plan_sha256'):
                args += ['--approve-plan-sha256', str(body['approve_plan_sha256'])]
            if body.get('publish'):
                if software != 'jianying' or engine != 'project':
                    return self._json({'error': '只有剪映原生草稿可以复制到剪映草稿库'}, 400)
                args.append('--publish')
            start_task(f'交付 {software}: {sheet_path.stem}', args, task_id=tid)
            return self._json({'queued': True, 'task': tid, 'report_url': '/api/delivery/' + tid})
        if path == "/api/video-brain/watch-screen":
            with VIDEO_BRAINS_LOCK:
                active = sum(1 for b in VIDEO_BRAINS.values() if b.state().get("running"))
                if active >= 4:
                    return self._json({"error": "同时观察任务已达 4 个上限，请先停止一个任务"}, 429)
                brain_id = uuid.uuid4().hex[:16]
                try:
                    from aicut.desktop_brain import DesktopBrain
                    interval = float(body.get("interval", 1.5))
                    brain = DesktopBrain(f"screen-{brain_id}", interval=interval,
                                         vision=bool(body.get("vision", False)))
                    VIDEO_BRAINS[brain_id] = brain
                    brain.start()
                except Exception as exc:
                    VIDEO_BRAINS.pop(brain_id, None)
                    return self._json({"error": f"启动剪映窗口观察失败：{exc}"}, 500)
            return self._json({"ok": True, "id": brain_id, "source": "jianying_window",
                               "vision": bool(body.get("vision", False))})
        if path == "/api/video-brain/watch":
            raw = str(body.get("video", "")).strip()
            try:
                video = safe_path(raw)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            if not video.is_file() or video.suffix.lower() not in {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}:
                return self._json({"error": "请选择允许目录内的视频素材文件"}, 400)
            try:
                interval = float(body.get("interval", 2.0))
            except (TypeError, ValueError):
                return self._json({"error": "采样间隔必须是数字"}, 400)
            if not 0.5 <= interval <= 30:
                return self._json({"error": "采样间隔需在 0.5 到 30 秒之间"}, 400)
            with VIDEO_BRAINS_LOCK:
                active = sum(1 for b in VIDEO_BRAINS.values() if b.state().get("running"))
                if active >= 4:
                    return self._json({"error": "同时观察任务已达 4 个上限，请先停止一个任务"}, 429)
                brain_id = uuid.uuid4().hex[:16]
                try:
                    from aicut.video_brain import VideoBrain
                    brain = VideoBrain(video, use_vlm=bool(body.get("vision", False)),
                                       name=f"observe-{brain_id}", frame_sample_sec=interval)
                    VIDEO_BRAINS[brain_id] = brain
                    brain.start()
                except Exception as exc:
                    VIDEO_BRAINS.pop(brain_id, None)
                    return self._json({"error": f"启动观察失败：{exc}"}, 500)
            return self._json({"ok": True, "id": brain_id, "video": video.name,
                               "vision": bool(body.get("vision", False))})
        if path == "/api/video-brain/stop":
            brain_id = str(body.get("id", ""))
            with VIDEO_BRAINS_LOCK:
                brain = VIDEO_BRAINS.get(brain_id)
            if brain is None:
                return self._json({"error": "观察任务不存在"}, 404)
            brain.stop()
            return self._json({"ok": True, "id": brain_id, "state": brain.state()})
        if path == '/api/production/cancel':
            tid = str(body.get('task_id', ''))
            if not tid or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in tid):
                return self._json({'error': '无效任务 id'}, 400)
            task = PROJECT / 'work' / 'tasks' / tid
            if not (task / 'status.json').is_file():
                return self._json({'error': '任务不存在'}, 404)
            (task / 'cancel').touch()
            return self._json({'ok': True, 'task_id': tid, 'state': 'cancellation_requested'})
        if path == "/api/style/apply":
            style = body.get("style")
            sheet = body.get("sheet")
            if not style or not sheet:
                return self._json({"error": "需要 style 与 sheet"}, 400)
            try:
                target = safe_path(sheet)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            cmd = [str(PY), str(PROJECT / "content-library" / "library.py"), "apply",
                   "--style", style, "--sheet", str(target)]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", cwd=str(PROJECT))
            return self._json({"ok": proc.returncode == 0, "output": (proc.stdout or "") + (proc.stderr or "")})
        if path == "/api/compliance":
            text = body.get("text", "")
            target = body.get("path")
            args = []
            if target:
                try:
                    args = [str(safe_path(target))]
                except PermissionError as exc:
                    return self._json({"error": str(exc)}, 403)
            elif text:
                tmp = APP_DIR / "tmp_text.txt"
                tmp.write_text(text, encoding="utf-8")
                args = [str(tmp)]
            else:
                return self._json({"error": "需要 text 或 path"}, 400)
            checker = Path(portable_path('user:.agents/skills/duanshipin-hegui-jianji/scripts/check_hegui.py'))
            proc = subprocess.run([str(PY), str(checker)] + args, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", cwd=str(PROJECT))
            return self._json({"ok": proc.returncode == 0, "hits": proc.returncode,
                               "output": proc.stdout + proc.stderr})
        if path == "/api/evaluate/pre":
            sheet = body.get("sheet")
            if not sheet:
                return self._json({"error": "需要 sheet"}, 400)
            try:
                target = safe_path(sheet)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            code, out = run_json("evaluate.py", ["pre", "--sheet", str(target)], timeout=180)
            return self._json({"ok": code == 0, "output": out})
        if path == "/api/evaluate/post":
            data = body.get("data")
            if not data:
                return self._json({"error": "需要 data"}, 400)
            try:
                target = safe_path(data)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            code, out = run_json("evaluate.py", ["post", "--data", str(target)], timeout=180)
            return self._json({"ok": code == 0, "output": out})
        if path == "/api/pipeline":
            source = body.get("source")
            if not source:
                return self._json({"error": "需要 source（素材文件或目录）"}, 400)
            try:
                target = safe_path(source)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            name = body.get("name")
            if target.is_file():
                task_id = uuid.uuid4().hex
                cmd = [str(PY), str(PROJECT / 'scripts' / 'pipeline.py'), '--video', str(target), '--task-id', task_id]
                if name:
                    cmd += ['--name', str(name)]
                if body.get('goal'):
                    cmd += ['--goal', str(body['goal'])]
                for field, flag in (('publish', '--publish'), ('export_mp4', '--export-mp4'), ('copilot', '--copilot')):
                    if body.get(field):
                        cmd.append(flag)
                return self._json({'ok': True, 'task': start_task(f'流水线: {target.name}', cmd, task_id),
                                   'report_url': f'/api/production/{task_id}'})
            runner = PY if PY.exists() else "python"
            if runner == PY:
                args = [str(PROJECT / "run.ps1"), "-Source", str(target)]
                if name:
                    args += ["-Name", name]
                if body.get("publish"):
                    args.append("-Publish")
                cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"] + args
            else:
                return self._json({"error": "找不到项目虚拟环境"}, 500)
            return self._json({"ok": True, "task": start_task(f"流水线: {Path(str(target)).name}", cmd)})
        if path == "/api/autoedit":
            video = body.get("video")
            style = body.get("style")
            if not video:
                return self._json({"error": "需要 video"}, 400)
            try:
                target = safe_path(video)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            args = ["--video", str(target)]
            args += ["--style", str(style)] if style else ["--auto-style"]
            if body.get('knowledge', True) is False:
                args.append('--no-knowledge')
            if body.get('semantic', True) is False:
                args.append('--no-semantic')
            for key, flag in (("name", "--name"), ("cover", "--cover"), ("cta", "--cta"), ("bgm", "--bgm"), ('goal', '--goal')):
                if body.get(key):
                    args += [flag, str(body[key])]
            if body.get('publish'):
                return self._json({'error': '请先检查方案，再通过确认交付复制草稿'}, 400)
            # Web planning never builds; confirmed delivery has a separate SHA-bound endpoint.
            args.append('--no-build')
            if body.get("smart"):
                args.append("--smart")
            for key in ("beats", "broll_index"):
                if body.get(key):
                    args += ["--" + key.replace("_", "-"), str(safe_path(body[key]))]
            if body.get("drop_blurry"):
                args.append("--drop-blurry")
            if body.get("keep_pauses"):
                args.append("--keep-pauses")
            if body.get("pause_threshold"):
                args += ["--pause-threshold", str(float(body["pause_threshold"]))]
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            sys.path.insert(0, str(PROJECT / 'lib'))
            from aicut.jobs import run_command
            output = []
            task_id = uuid.uuid4().hex
            args += ['--task-id', task_id]
            if body.get('async'):
                cmd = [str(PY), str(PROJECT / 'scripts' / 'pipeline.py'), *args]
                return self._json({'ok': True, 'task': start_task('智能剪辑', cmd, task_id),
                                   'report_url': f'/api/production/{task_id}'})
            try:
                run_command([str(PY), str(PROJECT / 'scripts' / 'pipeline.py'), *args],
                            cwd=str(PROJECT), env=env, timeout=14400, on_line=output.append)
                ok = True
            except Exception as exc:
                from aicut.jobs import mark_interrupted
                mark_interrupted(PROJECT, task_id, 'failed', str(exc))
                output.append(str(exc))
                ok = False
            return self._json({'ok': ok, 'output': '\n'.join(output), 'task_id': task_id,
                               'report': str(PROJECT / 'work' / 'tasks' / task_id / 'status.json')})
        if path == "/api/research":
            topic = body.get("topic")
            if not topic:
                return self._json({"error": "需要 topic（本片主题）"}, 400)
            args = [str(PY), str(PROJECT / "scripts" / "research.py"), "--topic", str(topic)]
            if body.get("transcript"):
                try:
                    args += ["--transcript", str(safe_path(body["transcript"]))]
                except PermissionError as exc:
                    return self._json({"error": str(exc)}, 403)
            if body.get("out"):
                args += ["--out", str(safe_path(body["out"]))]
            return self._json({"ok": True, "task": start_task(f"联网比对: {topic}", args)})
        if path == "/api/understand":
            video = body.get("video")
            if not video:
                return self._json({"error": "需要 video"}, 400)
            try:
                target = safe_path(video)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            args = [str(PY), str(PROJECT / "scripts" / "understand.py"), "--video", str(target),
                    "--out", str(PROJECT / "work" / f"understand-{target.stem}.json")]
            if body.get("frames"):
                args += ["--frames", str(int(body["frames"]))]
            return self._json({"ok": True, "task": start_task(f"AI 识别: {target.name}", args)})
        if path == "/api/export":
            if body.get("no_subs"):
                return self._json({'error': '请在决策单中明确调整字幕后重新确认'}, 400)
            return self.api_post('/api/delivery', {**body, 'engine': 'portable'})
        if path == "/api/beats":
            audio = body.get("audio")
            if not audio:
                return self._json({"error": "需要 audio（音频或视频路径）"}, 400)
            try:
                audio_path = safe_path(audio)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            out = PROJECT / "work" / f"beats-{audio_path.stem}.json"
            args = [str(PY), str(PROJECT / "scripts" / "beats.py"), "--audio", str(audio_path), "--out", str(out)]
            return self._json({"ok": True, "task": start_task(f"节拍分析: {audio_path.name}", args)})
        if path == "/api/broll-index":
            target = body.get("dir")
            if not target:
                return self._json({"error": "需要 dir（素材目录或文件）"}, 400)
            try:
                target_path = safe_path(target)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            args = [str(PY), str(PROJECT / "scripts" / "broll_index.py"), "--dir", str(target_path),
                    "--max-per-file", str(int(body.get("max_per_file") or 3)),
                    "--max-total", str(int(body.get("max_total") or 12)),
                    "--out", str(PROJECT / "work" / "broll-index.json")]
            return self._json({"ok": True, "task": start_task(f"B-roll 索引: {target_path.name}", args)})
        if path == "/api/transcribe":
            audio = body.get("audio")
            if not audio:
                return self._json({"error": "需要 audio"}, 400)
            try:
                target = safe_path(audio)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            return self._json({"ok": True, "task": start_task(f"转写: {target.name}",
                                                             [str(PY), str(PROJECT / "scripts" / "transcribe.py"), str(target)])})
        return self._json({"error": "未知接口"}, 404)


def main() -> int:
    global CONFIG
    if '--help' in sys.argv or '-h' in sys.argv:
        print('usage: python app/server.py [--host HOST] [--port PORT]')
        print('Default: loopback only. LAN binding requires a generated access token.')
        return 0
    host = CONFIG["host"]
    port = int(CONFIG["port"])
    for i, arg in enumerate(sys.argv):
        if arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    if not PY.exists():
        print(f"[警告] 找不到项目虚拟环境: {PY}")

    lan = is_lan_bind(host)
    if lan:
        CONFIG, rotated = prepare_lan_token(CONFIG)
        if rotated:
            print('[安全] 已将演示令牌轮换为本次局域网专用随机令牌。')
    server = ThreadingHTTPServer((host, port), Handler)
    print("=" * 56)
    print("  AI cut 主机服务已启动")
    print(f"  本机访问:   http://127.0.0.1:{port}")
    if lan:
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            print(f"  局域网访问: http://{ip}:{port}  （同一 WiFi 下的手机/平板/其他电脑）")
        except Exception:  # noqa: BLE001
            print("  局域网访问: http://<本机IP>:" + str(port))
        print(f"  访问令牌:   {CONFIG['token']}")
    print(f"  项目目录:   {PROJECT}")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
        with VIDEO_BRAINS_LOCK:
            brains = list(VIDEO_BRAINS.values())
        for brain in brains:
            try:
                brain.stop()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
