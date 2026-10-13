#!/usr/bin/env python3
r"""视频大脑 HTTP 服务（stdlib 版，零依赖）。

GET /videos/<name>/status        当前解析进度与最近事件
GET /videos/<name>/hints         包装建议（情绪峰值→花字/特效，平淡→B-roll）
GET /videos/<name>/events?t=3.2  某时间点的事件
GET /videos/<name>/summary
POST /videos/<name>/watch        启动常驻观察
GET /health                      存活探测
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import video_brain as vb  # noqa: E402

BRAINS: dict[str, vb.VideoBrain] = {}


def _brain(name: str) -> vb.VideoBrain | None:
    return BRAINS.get(name)


def _resolve_video(name: str) -> Path | None:
    for sub in ("work/normalized", "01-audio", ROOT, "work"):
        for cand in (Path(sub) / f"{name}.mp4", Path(sub) / f"{name}.mov"):
            if cand.exists():
                return cand
    nb = Path("work/normalized")
    if nb.exists():
        found = sorted(nb.glob(f"{name}*"))
        if found:
            return found[0]
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path)
        query = parse_qs(p.query)
        parts = [x for x in p.path.split("/") if x]
        if not parts:
            return self._send(200, {"ok": True, "service": "video_brain", "brains": list(BRAINS)})
        if parts[0] == "health":
            return self._send(200, {"ok": True})
        if parts[0] == "videos" and len(parts) >= 3:
            name, kind = parts[1], parts[2]
            brain = _brain(name)
            if brain is None:
                v = _resolve_video(name)
                if not v:
                    return self._send(404, {"error": f"找不到视频: {name}"})
                brain = vb.VideoBrain(v, name=name)
                BRAINS[name] = brain
            if kind == "status":
                return self._send(200, brain.state())
            if kind == "hints":
                return self._send(200, {"hints": brain.packaging_hints()})
            if kind == "events":
                try:
                    t = float(query.get("t", ["0"])[0])
                    after = float(query.get("after", ["0"])[0])
                except ValueError:
                    return self._send(400, {"error": "t/after 必须是数字"})
                events = brain.events_at(t)
                if after:
                    events = [e for e in events if float(e.get("ts", 0)) > after]
                    return self._send(200, {"events": events,
                                            "latest_ts": events[-1]["ts"] if events else after})
                return self._send(200, events)
            if kind == "frame":
                frame = brain.latest_frame_file
                if not frame.exists():
                    return self._send(404, {"error": "尚未产出最新画面"})
                return self._send_bytes(200, frame.read_bytes(), "image/jpeg")
            if kind == "summary":
                return self._send(200, {"summary": brain.summary()})
            return self._send(404, {"error": f"未知查询: {kind}"})
        return self._send(404, {"error": "路由未匹配"})

    def do_POST(self):
        p = urlparse(self.path)
        parts = [x for x in p.path.split("/") if x]
        if len(parts) == 3 and parts[0] == "videos" and parts[2] == "watch":
            name = parts[1]
            v = _resolve_video(name)
            if not v:
                return self._send(404, {"error": f"找不到视频: {name}"})
            query = parse_qs(p.query)
            try:
                interval = float(query.get("interval", ["2.0"])[0])
            except ValueError:
                return self._send(400, {"error": "interval 必须是数字"})
            if not 0.25 <= interval <= 60:
                return self._send(400, {"error": "interval 必须在 0.25 到 60 秒之间"})
            brain = vb.VideoBrain(v, name=name,
                                  use_vlm=query.get("vlm", ["0"])[0] == "1",
                                  frame_sample_sec=interval)
            brain.start()
            BRAINS[name] = brain
            return self._send(200, {"started": True, "video": str(v),
                                    "frame_interval": interval,
                                    "frame_url": f"/videos/{name}/frame"})
        if len(parts) == 3 and parts[0] == "videos" and parts[2] == "stop":
            brain = _brain(parts[1])
            if brain is None:
                return self._send(404, {"error": "观察任务不存在"})
            brain.stop()
            return self._send(200, {"stopped": True, "state": brain.state()})
        return self._send(404, {"error": "路由未匹配"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8645)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[大脑服务] 监听 {args.host}:{args.port}  "
          f"(GUI: http://127.0.0.1:{args.port}/videos/<name>/status)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        server.server_close()
        for brain in list(BRAINS.values()):
            brain.stop()


if __name__ == "__main__":
    main()
