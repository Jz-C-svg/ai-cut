#!/usr/bin/env python3
r"""实时视频大脑 CLI。

用法:
  python video_brain.py watch <视频> [--vlm]     # 常驻观察，文件稳定后自动收尾
  python video_brain.py status <视频>            # 查看状态/最近事件/包装建议
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut.video_brain import VideoBrain  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="实时视频大脑")
    ap.add_argument("cmd", choices=["watch", "status"])
    ap.add_argument("video", help="视频路径")
    ap.add_argument("--vlm", action="store_true", help="启用视觉模型逐帧标注（需 Ollama 视觉模型）")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="画面采样间隔（秒），默认 2；越小越实时但 CPU/ffmpeg 开销越高")
    ap.add_argument("--jsonl", action="store_true",
                    help="watch 时把每个 frame/vision/事件即时输出为 JSONL")
    args = ap.parse_args()

    brain = VideoBrain(args.video, use_vlm=args.vlm, frame_sample_sec=args.interval)
    if args.cmd == "status":
        print(json.dumps(brain.state(), ensure_ascii=False, indent=2))
        print("\n包装建议:")
        for h in brain.packaging_hints():
            print(f"  {h['t']:>6.1f}s  {h['类型']}  ← {h['理由']}")
        return 0

    # watch：常驻观察
    print(f"[大脑] 开始观察 {args.video}（文件稳定 {6}s 后自动收尾；Ctrl+C 可中断，状态已持续落盘）")
    if args.jsonl:
        brain.subscribe(lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
    brain.start()
    try:
        while brain._thread and brain._thread.is_alive():
            print(brain.summary(), flush=True)
            brain._thread.join(timeout=5)
    except KeyboardInterrupt:
        brain.stop()
    brain.process_available()
    print("[大脑] 观察结束。状态：", brain.out_dir / "state.json")
    print(brain.summary())
    print("包装建议:")
    for h in brain.packaging_hints():
        print(f"  {h['t']:>6.1f}s  {h['类型']}  ← {h['理由']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
