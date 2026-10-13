r"""video_brain · 实时视频观察与解析大脑。

设计：一个常驻观察者，对（正在增长或已完成的）视频做增量解析，
维护统一的状态时间轴与事件流，供剪辑决策随时查询。

感知通道（全部带降级，缺模型也能跑）：
  1. 音频信号：ffmpeg 解码为 pcm → numpy 算逐秒 RMS 响度；静音段、响度峰值
  2. 画面信号：按采样间隔抽帧（ffmpeg 精确 seek 单帧）→ 帧差算运动强度、
     平均亮度；相邻帧差异大 = 场景切换事件
  3. 语义通道（可选）：抽帧交 VLM（Ollama 视觉模型 / DeepSeek 视觉）得
     场景/主体/情绪标签；不可用时跳过，不影响 1/2
  4. 文本通道（可选）：发现 02-transcript/<名>.json 时叠加逐句文本、
     语速（字/秒）与关键词命中 → 情绪峰值判定更准

状态产物（work/video-brain/<素材名>/）：
  state.json   当前状态：已解析到几秒、逐秒指标、滚动摘要
  events.jsonl 事件流（一行一个事件）：scene_change / loudness_peak /
               silence / emotion_peak / info_drop ...

查询接口：
  brain.state()        -> dict   当前完整状态
  brain.events_at(t)   -> list  某时刻附近的事件（供花字/特效决策）
  brain.summary()      -> str   人类可读摘要

CLI（scripts/video_brain.py）：
  watch <视频>   常驻观察：文件增长就继续解析，稳定后自动收尾
  status <视频>  打印当前状态与最近事件
"""
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections.abc import Callable

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_ROOT = ROOT / "work" / "video-brain"

FFMPEG = "ffmpeg"
SAMPLE_SEC = 1.0        # 音频/指标采样粒度（秒）
FRAME_SAMPLE_SEC = 2.0  # 抽帧间隔（秒）
IDLE_FINISH = 6.0       # 文件大小稳定多少秒视为完成
FRAME_DIR_NAME = "frames"


# ---------------- 基础工具 ----------------

def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            [FFMPEG, "-i", str(path)], capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=20)
        m = None
        for line in (out.stderr or "").splitlines():
            if "Duration:" in line:
                m = line
        if m:
            h, mnt, s = m.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(mnt) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def _audio_rms_per_sec(path: Path, start: float, dur: float) -> list[float]:
    """解码 [start, start+dur) 的音频为 16k 单声道 pcm，返回逐秒 RMS（0-1）。"""
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
             "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
            capture_output=True, timeout=60)
        pcm = out.stdout
        if not pcm:
            return []
        x = np.array(struct.unpack(f"{len(pcm)//2}h", pcm), dtype=np.float32) / 32768.0
        n = int(16000 * SAMPLE_SEC)
        secs = []
        for i in range(0, len(x) - n + 1, n):
            rms = float(np.sqrt(np.mean(x[i:i + n] ** 2)))
            secs.append(min(rms * 4.0, 1.0))  # 归一到 0-1（语音典型 RMS~0.1）
        return secs
    except Exception:
        return []


def _grab_frame(path: Path, t: float, size: int = 96) -> np.ndarray | None:
    """精确 seek 抽一帧，缩到 size 宽度灰度图（算运动/亮度用）。"""
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(path),
             "-frames:v", "1", "-vf", f"scale={size}:-1", "-f", "rawvideo",
             "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=20)
        buf = out.stdout
        if not buf:
            return None
        w = size
        h = len(buf) // w
        return np.frombuffer(buf[:w * h], dtype=np.uint8).reshape(h, w)
    except Exception:
        return None


def _grab_frame_jpeg(path: Path, t: float, width: int = 640) -> bytes | None:
    """取一帧可供观测台读取的 JPEG；失败时返回 None。"""
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(path),
             "-frames:v", "1", "-vf", f"scale={width}:-2",
             "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "-"],
            capture_output=True, timeout=20)
        return out.stdout or None
    except (OSError, subprocess.SubprocessError):
        return None


def _vlm_tag(frame_png: bytes, cancel_event=None) -> dict | None:
    """语义通道：帧 → {场景, 主体, 情绪, 信息密度}。VLM 不可用返回 None。"""
    try:
        from . import bundled_vision
        prompt = ("分析这帧画面，只输出 JSON，不要推测看不到的内容："
                  '{"场景":"...","主体":"...","情绪":"平静|紧张|惊喜|低沉",'
                  '"信息密度":"高|中|低"}')
        return bundled_vision.analyze_image_bytes(frame_png, prompt, timeout=210,
                                                  cancel_event=cancel_event, persist_diagnostic=False)
    except Exception:
        return None
    return None


# ---------------- 大脑主体 ----------------

class VideoBrain:
    """增量式视频观察者。start() 后台观察；state()/events_at()/packaging_hints() 查询。"""

    def __init__(self, video: str | Path, use_vlm: bool = False, name: str | None = None,
                 frame_sample_sec: float = FRAME_SAMPLE_SEC):
        self.video = Path(video)
        self.name = name or self.video.stem
        self.use_vlm = use_vlm
        if frame_sample_sec <= 0:
            raise ValueError("frame_sample_sec 必须大于 0")
        self.frame_sample_sec = float(frame_sample_sec)
        self.out_dir = OUT_ROOT / self.name
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.frame_dir = self.out_dir / FRAME_DIR_NAME
        self.frame_dir.mkdir(parents=True, exist_ok=True)
        self.latest_frame_file = self.frame_dir / "latest.jpg"
        self.state_file = self.out_dir / "state.json"
        self.events_file = self.out_dir / "events.jsonl"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.parsed_to = 0.0
        self.loudness: list[float] = []
        self.motion: list[float] = []
        self.brightness: list[float] = []
        self.frames_meta: dict[str, dict] = {}
        self.events: list[dict] = []
        self._listeners: list[Callable[[dict], None]] = []
        self._listener_errors: list[dict] = []
        self._event_seq = 0
        self._vision_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aicut-vlm") if use_vlm else None
        self._vision_busy = threading.Lock()
        self._vision_pending = 0
        self._vision_latest: tuple[float, bytes] | None = None
        self.running = False
        self._emotion_done = False
        self._lock = threading.RLock()
        self._load_existing()
        self._event_seq = max((int(e.get("seq", 0)) for e in self.events), default=0)

    def subscribe(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        """订阅实时事件，返回取消订阅函数。

        监听器在观察线程中同步调用；需要耗时处理时应自行放入队列，不能阻塞感知。
        """
        if not callable(listener):
            raise TypeError("listener 必须可调用")
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return unsubscribe

    def _load_existing(self) -> None:
        if self.state_file.exists():
            try:
                st = json.loads(self.state_file.read_text(encoding="utf-8-sig"))
                self.parsed_to = st.get("parsed_to", 0.0)
                self.loudness = st.get("loudness", [])
                self.motion = st.get("motion", [])
                self.brightness = st.get("brightness", [])
                self.events = st.get("events", [])
                self.frames_meta = st.get("frames_meta", {})
            except json.JSONDecodeError:
                pass

    def _notify(self, event: dict) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(dict(event))
            except Exception as exc:  # listener 是外部扩展，不能中断感知线程
                error = {"listener": repr(listener), "error": str(exc)[:240],
                         "event": event.get("kind"), "ts": time.time()}
                with self._lock:
                    self._listener_errors.append(error)
                    self._listener_errors = self._listener_errors[-20:]

    def _persist(self) -> None:
        with self._lock:
            payload = json.dumps({
                "video": str(self.video), "name": self.name, "parsed_to": round(self.parsed_to, 2),
                "loudness": self.loudness, "motion": self.motion,
                "brightness": self.brightness, "events": self.events[-500:],
                "frames_meta": self.frames_meta, "frame_sample_sec": self.frame_sample_sec,
                "latest_frame": str(self.latest_frame_file) if self.latest_frame_file.exists() else None,
                "updated": time.time(),
            }, ensure_ascii=False)
            temporary = self.state_file.with_suffix(".json.tmp")
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.state_file)

    def _emit(self, kind: str, t: float, detail: dict) -> None:
        with self._lock:
            self._event_seq += 1
            ev = {"seq": self._event_seq, "t": round(t, 2), "kind": kind, **detail, "ts": time.time()}
            self.events.append(ev)
            with self.events_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self._notify(ev)

    def _queue_vision(self, t: float, frame_png: bytes) -> None:
        """Run semantic vision off the decoder thread; skip stale frames while busy."""
        if self._vision_pool is None:
            return
        if not self._vision_busy.acquire(blocking=False):
            self._vision_latest = (t, frame_png)
            return
        self._vision_pending += 1
        self._emit("vision_pending", t, {"pending": self._vision_pending})
        try:
            future = self._vision_pool.submit(_vlm_tag, frame_png, self._stop)
        except Exception:
            self._vision_busy.release()
            self._vision_pending = max(0, self._vision_pending - 1)
            self._emit("vision_error", t, {"reason": "视觉任务提交失败"})
            return
        def complete(done):
            self._vision_pending = max(0, self._vision_pending - 1)
            try:
                tag = done.result()
                if tag:
                    self.frames_meta[str(round(t, 1))] = tag
                    self._emit("vision", t, {"tag": tag})
                else:
                    self._emit("vision_unavailable", t, {"reason": "模型未返回可用结果"})
            except Exception as exc:  # worker errors remain visible to the UI
                self._emit("vision_error", t, {"reason": str(exc)[:240]})
            finally:
                self._vision_busy.release()
                self._persist()
                latest, self._vision_latest = self._vision_latest, None
                if latest is not None and not self._stop.is_set():
                    self._queue_vision(*latest)
        future.add_done_callback(complete)

    # ---- 增量解析 ----

    def _parse_window(self, start: float, end: float) -> None:
        rms = _audio_rms_per_sec(self.video, start, end - start)
        for i, v in enumerate(rms):
            t = start + i * SAMPLE_SEC
            self.loudness.append(v)
            if v > 0.55:
                self._emit("loudness_peak", t, {"value": round(v, 2), "note": "响度峰值=可能情绪高点"})
            if v < 0.02:
                self._emit("silence", t, {"value": v, "note": "静音"})
        t = start
        while t < end:
            f = _grab_frame(self.video, t)
            if f is not None:
                brightness = float(f.mean()) / 255.0
                self.brightness.append(brightness)
                prev = _grab_frame(self.video, max(t - self.frame_sample_sec, 0))
                motion = 0.0
                if prev is not None and prev.shape == f.shape:
                    diff = float(np.abs(f.astype(np.int16) - prev.astype(np.int16)).mean()) / 255.0
                    motion = min(diff * 3.0, 1.0)
                    self.motion.append(motion)
                    if diff > 0.12:
                        self._emit("scene_change", t, {"diff": round(diff, 3)})
                jpeg = _grab_frame_jpeg(self.video, t)
                if jpeg:
                    tmp = self.frame_dir / "latest.jpg.tmp"
                    tmp.write_bytes(jpeg)
                    os.replace(tmp, self.latest_frame_file)
                self._emit("frame", t, {
                    "brightness": round(brightness, 4),
                    "motion": round(motion, 4),
                    "frame": str(self.latest_frame_file) if self.latest_frame_file.exists() else "",
                })
                if self.use_vlm:
                    try:
                        png = subprocess.run(
                            [FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(self.video),
                             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                            capture_output=True, timeout=20).stdout
                        if png:
                            self._queue_vision(t, png)
                    except Exception:
                        pass
            t += self.frame_sample_sec
        self.parsed_to = end
        self._persist()

    def _speech_rate(self) -> list[tuple[float, float]] | None:
        tj = ROOT / "02-transcript" / f"{self.name}.json"
        if not tj.exists():
            return None
        try:
            data = json.loads(tj.read_text(encoding="utf-8-sig"))
            segs = data.get("segments") if isinstance(data, dict) else data
            out = []
            for s in segs or []:
                dur = float(s.get("end", 0)) - float(s.get("start", 0))
                n = len(str(s.get("text", "")))
                if dur > 0.2:
                    out.append((float(s.get("start", 0)), n / dur))
            return out
        except Exception:
            return None

    def _analyze_emotion(self) -> None:
        """收尾分析：响度峰值 +（有逐字稿时）语速突增 → 情绪峰值事件。"""
        rate = self._speech_rate()
        for i, v in enumerate(self.loudness):
            if v <= 0.55:
                continue
            t = i * SAMPLE_SEC
            boosted = False
            if rate:
                near = [r for tt, r in rate if abs(tt - t) <= 1.0]
                if near and max(near) > 5.5:
                    boosted = True
            if boosted or not rate:
                self._emit("emotion_peak", t, {
                    "loudness": round(v, 2), "speech_fast": boosted,
                    "note": "情绪高点：建议花字/特效"})

    def process_available(self, max_window: float = 4.0) -> float:
        """解析到当前可得时长（文件增长时可反复调用）。返回已解析秒数。"""
        dur = _probe_duration(self.video)
        if dur > self.parsed_to + 0.05:
            self._parse_window(self.parsed_to, min(dur, self.parsed_to + max(0.5, max_window)))
        if dur > 0 and self.parsed_to >= dur - 0.5 and not self._emotion_done:
            self._analyze_emotion()
            self._emotion_done = True
        return self.parsed_to

    # ---- 生命周期 ----

    def _watch_loop(self) -> None:
        last_size = -1
        stable_since: float | None = None
        while not self._stop.is_set():
            try:
                try:
                    size = self.video.stat().st_size if self.video.exists() else 0
                except OSError:
                    size = 0
                if size != last_size:
                    last_size = size
                    stable_since = None
                elif stable_since is None:
                    stable_since = time.time()
                # Process bounded windows so completed files also report incremental progress.
                self.process_available(max_window=4.0)
                if (stable_since is not None and time.time() - stable_since > IDLE_FINISH
                        and self.parsed_to >= _probe_duration(self.video) - 0.5):
                    self._persist()
                    self.running = False
                    return
                self._stop.wait(1.0)
            except Exception as exc:
                self._emit("analysis_error", self.parsed_to, {"reason": str(exc)[:300]})
                self._persist()
                self.running = False
                return
        self._persist()
        self.running = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.running = bool(self._thread and self._thread.is_alive())
        if self._vision_pool:
            self._vision_pool.shutdown(wait=False, cancel_futures=True)

    # ---- 查询 ----

    def state(self) -> dict:
        dur = len(self.loudness) * SAMPLE_SEC if self.loudness else self.parsed_to
        kinds: dict[str, int] = {}
        for e in self.events:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        return {
            "video": str(self.video), "parsed_to": round(self.parsed_to, 2),
            "duration_est": round(dur, 2),
            "loudness_avg": round(sum(self.loudness) / len(self.loudness), 3) if self.loudness else 0,
            "motion_avg": round(sum(self.motion) / len(self.motion), 3) if self.motion else 0,
            "event_count": len(self.events), "event_kinds": kinds,
            "vision_tags": len(self.frames_meta),
            "latest_frame": str(self.latest_frame_file) if self.latest_frame_file.exists() else None,
            "frame_sample_sec": self.frame_sample_sec,
            "running": self.running or bool(self._thread and self._thread.is_alive()),
            "vision_pending": self._vision_pending,
            "emotion_done": self._emotion_done,
            "listener_errors": list(self._listener_errors),
            "recent_events": self.events[-8:],
        }

    def events_at(self, t: float, window: float = 1.5) -> list[dict]:
        return [e for e in self.events if abs(e["t"] - t) <= window]

    def packaging_hints(self) -> list[dict]:
        """给剪辑决策的包装建议：情绪峰值→花字/特效，长平淡→B-roll。"""
        hints: list[dict] = []
        seen: set[float] = set()
        for e in self.events:
            if e["kind"] == "emotion_peak" and e["t"] not in seen:
                seen.add(e["t"])
                hints.append({"t": e["t"], "类型": "花字或特效", "理由": "情绪高点"})
        run = 0
        for i, v in enumerate(self.loudness):
            if 0.02 < v < 0.15:
                run += 1
                if run * SAMPLE_SEC >= 2.0:
                    t = (i - run + 1) * SAMPLE_SEC
                    if t not in seen:
                        seen.add(t)
                        hints.append({"t": round(t, 2), "类型": "B-roll或删减", "理由": "长平淡段"})
            else:
                run = 0
        return hints

    def summary(self) -> str:
        st = self.state()
        return (f"[{self.name}] 已解析 {st['parsed_to']}s｜平均响度 {st['loudness_avg']}"
                f"｜平均运动 {st['motion_avg']}｜事件 {st['event_kinds']}"
                f"｜视觉标签 {st['vision_tags']}")
