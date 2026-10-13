#!/usr/bin/env python3
"""实时视频大脑的无素材自测：事件总线、参数护栏、最新画面状态。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import video_brain as vb  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))
    if not ok:
        raise AssertionError(name)


def main() -> int:
    old_root = vb.OUT_ROOT
    temp_root = Path(tempfile.mkdtemp(prefix="aicut-brain-test-"))
    vb.OUT_ROOT = temp_root
    try:
        try:
            vb.VideoBrain("missing.mp4", frame_sample_sec=0)
        except ValueError:
            check("拒绝非正采样间隔", True)
        else:
            check("拒绝非正采样间隔", False)

        brain = vb.VideoBrain("input.mp4", frame_sample_sec=0.5, name="case")
        received: list[dict] = []
        unsubscribe_first = brain.subscribe(received.append)
        brain._emit("frame", 0.5, {"brightness": 0.4, "motion": 0.1})
        check("事件即时推送", len(received) == 1 and received[0]["kind"] == "frame")
        check("事件落盘", brain.events_file.exists())

        brain._emit("scene_change", 1.0, {"diff": 0.2})
        unsubscribe = brain.subscribe(lambda _: (_ for _ in ()).throw(RuntimeError("bad listener")))
        brain._emit("frame", 1.5, {})
        check("监听器异常不打断感知", len(brain.events) == 3)
        check("监听器异常可观测", len(brain.state()["listener_errors"]) == 1)
        unsubscribe()
        unsubscribe_first()
        before = len(received)
        brain._emit("frame", 2.0, {})
        check("取消订阅生效", len(received) == before)

        frame = brain.frame_dir / "latest.jpg"
        frame.write_bytes(b"jpeg")
        brain._persist()
        state = json.loads(brain.state_file.read_text(encoding="utf-8"))
        check("状态包含采样间隔", state["frame_sample_sec"] == 0.5)
        check("状态包含最新画面", state["latest_frame"] == str(frame))
        return 0
    finally:
        vb.OUT_ROOT = old_root
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
