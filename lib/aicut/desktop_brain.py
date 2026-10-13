"""Observe the visible Jianying window and optionally describe it with local vision."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[2]


class DesktopBrain:
    def __init__(self, name: str, interval: float = 1.5, vision: bool = False):
        if not 0.5 <= interval <= 10:
            raise ValueError("屏幕采样间隔需在 0.5 到 10 秒之间")
        self.name, self.interval, self.use_vision = name, interval, vision
        self.out_dir = ROOT / "work" / "desktop-brain" / name
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.latest_frame_file = self.out_dir / "latest.jpg"
        self.events_file = self.out_dir / "events.jsonl"
        self.events: list[dict] = []
        self._event_seq = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._vision_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-vlm") if vision else None
        self._vision_busy = threading.Lock()
        self._vision_pending = 0
        self._vision_latest: bytes | None = None
        self.running = False
        self.window: dict | None = None
        self.error = ""

    def _emit(self, kind: str, detail: dict) -> None:
        with self._lock:
            self._event_seq += 1
            event = {"seq": self._event_seq, "kind": kind, "ts": time.time(), **detail}
            self.events.append(event)
            with self.events_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _find_window(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import ui_fast
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pids = []
        for line in (out.stdout or "").splitlines():
            cols = [c.strip('"') for c in line.split('","')]
            if len(cols) > 1 and cols[1].isdigit():
                pids.append(int(cols[1]))
        for pid in pids:
            window = ui_fast.main_window(pid)
            if window:
                return window
        return None

    def _queue_vision(self, jpeg: bytes):
        if not self._vision_pool:
            return
        if not self._vision_busy.acquire(blocking=False):
            # Keep one fresh screenshot; replace it as the window changes so the
            # next inference never works through a stale backlog.
            self._vision_latest = jpeg
            return
        self._vision_pending = 1
        self._emit("vision_pending", {})
        def analyze():
                from .bundled_vision import analyze_image_bytes
                return analyze_image_bytes(jpeg,
                    "你是短视频成片质检员。图像是剪映专业版完整桌面截图。请优先检查右侧播放器中的视频画面，"
                    "并结合下方时间线判断当前播放帧的成片观感。左侧素材/模板库和中间属性面板仅作为辅助；"
                    "忽略覆盖在剪映上的聊天气泡、桌面宠物、通知、其他应用浮窗及其文字，绝不根据这些浮窗内容作判断。"
                    "只描述截图确实可见的信息，不要推测视频上下文；看不清就明确写看不清。"
                    "检查人物/主体是否清晰、构图与留白、字幕是否遮挡或难读、画面是否有明显杂物，"
                    "以及时间线上字幕/音频轨是否明显缺失或错位。输出简体中文 JSON，不要思考过程："
                    '{"播放器画面":"播放器内主体、动作和场景的可见描述",'
                    '"成片观感":"构图、清晰度、主体位置的简短评价",'
                    '"字幕检查":"播放器中可见字幕的准确可读性与遮挡；没有则写当前帧未见字幕",'
                    '"时间线检查":"仅写截图能确认的轨道状态；无法判断则写无法确认",'
                    '"优先修改建议":"一条针对成片、可执行且基于可见证据的修改建议"}',
                    timeout=210, cancel_event=self._stop, persist_diagnostic=False)
        try:
            future = self._vision_pool.submit(analyze)
        except Exception:
            self._vision_busy.release()
            self._vision_pending = 0
            self._emit("vision_error", {"reason": "本地视觉任务提交失败"})
            return
        def complete(done):
            try:
                result = done.result()
                if not self._stop.is_set():
                    self._emit("screen_vision", {"result": result})
            except Exception as exc:
                if not self._stop.is_set():
                    self._emit("vision_error", {"reason": str(exc)[:240]})
            finally:
                self._vision_pending = 0
                self._vision_busy.release()
                latest, self._vision_latest = self._vision_latest, None
                if latest is not None and not self._stop.is_set():
                    self._queue_vision(latest)
        future.add_done_callback(complete)

    def _loop(self):
        next_vision = 0.0
        try:
            while not self._stop.is_set():
                window = self._find_window()
                if not window:
                    self.error = "未找到可见的剪映主窗口"
                    self._emit("window_unavailable", {"reason": self.error})
                    self._stop.wait(3)
                    continue
                self.window = {k: window.get(k) for k in ("pid", "title", "rect", "width", "height", "uia_class")}
                left, top, right, bottom = window["rect"]
                image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
                buff = io.BytesIO()
                image.convert("RGB").save(buff, format="JPEG", quality=78)
                jpeg = buff.getvalue()
                tmp = self.latest_frame_file.with_suffix(".tmp")
                tmp.write_bytes(jpeg)
                tmp.replace(self.latest_frame_file)
                self.error = ""
                self._emit("screen_frame", {"window": self.window, "frame": str(self.latest_frame_file)})
                now = time.monotonic()
                if self.use_vision and now >= next_vision:
                    self._queue_vision(jpeg)
                    next_vision = now + 8
                self._stop.wait(self.interval)
        except Exception as exc:
            self.error = str(exc)[:300]
            self._emit("capture_error", {"reason": self.error})
        finally:
            self.running = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, name=f"desktop-brain-{self.name}", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=4)
        self.running = bool(self._thread and self._thread.is_alive())
        if self._vision_pool:
            self._vision_pool.shutdown(wait=False, cancel_futures=True)

    def state(self):
        return {"name": self.name, "source": "jianying_window", "running": self.running or bool(self._thread and self._thread.is_alive()),
                "latest_frame": str(self.latest_frame_file) if self.latest_frame_file.exists() else None,
                "vision_pending": self._vision_pending, "vision_enabled": self.use_vision,
                "event_count": len(self.events), "window": self.window, "error": self.error,
                "frame_sample_sec": self.interval}
