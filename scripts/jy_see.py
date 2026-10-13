#!/usr/bin/env python3
r"""毫秒级"看"：不调云端视觉，用窗口 API + 像素分析直接回答关键状态。

回答的问题：
  · 剪映现在在首页还是编辑页（窗口类，~1ms）
  · 窗口几何（矩形/尺寸，~1ms）
  · 时间线有几条轨道、视频轨在哪一行、内容左右边界、播放头位置、播放头在几秒
  · 是否看不到时间线（可能被弹窗遮挡）

用法:
  python jy_see.py --pid 21156 [--json] [--repeat 20] [--duration 7.267]
"""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import argparse
import io
import json
import sys
import time
from pathlib import Path

if str(getattr(sys.stdout, "encoding", "")).lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ui_fast as F  # noqa: E402

SHOT = Path(portable_path('user:AppData/Local/Temp/jy-see.png'))


def see(pid: int, duration: float | None = None, want_shot: bool = True) -> dict:
    t0 = time.perf_counter()
    out: dict = {"pid": pid}
    win = F.main_window(pid)
    if not win:   # 剪映重启后 pid 会变，自动跟随
        import subprocess
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, encoding="gbk", errors="replace")
        for line in (r.stdout or "").splitlines():
            cols = [c.strip('"') for c in line.split('","')]
            if len(cols) >= 2 and cols[1].isdigit():
                cand = F.main_window(int(cols[1]))
                if cand:
                    pid = int(cols[1])
                    win = cand
                    out["pid"] = pid
                    out["pid_跟随"] = True
                    break
    if not win:
        out.update({"page": "无窗口", "ms": round((time.perf_counter() - t0) * 1000, 2)})
        return out
    l, t, r, b = win["rect"]
    out.update({"page": F.page_state(pid), "window": [l, t, r - l, b - t],
                "ms_window": round((time.perf_counter() - t0) * 1000, 2)})
    if out["page"] != "编辑页":
        out["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return out

    # 只抓时间线那一条带，而且直接在内存里处理（不落盘，省掉 PNG 编码）
    t1 = time.perf_counter()
    H = b - t
    band_box = (l, t + int(H * 0.52), l + (r - l), t + int(H * 0.95))
    import numpy as np
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=band_box, all_screens=True)
    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    out["ms_shot"] = round((time.perf_counter() - t1) * 1000, 2)

    t2 = time.perf_counter()
    small = arr[::2, ::2]                     # 隔行隔列采样，计算量降到 1/4
    sat = small.max(axis=2) - small.min(axis=2)
    mask = sat > 45
    row_counts = mask.sum(axis=1)
    if row_counts.size == 0:
        out["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return out
    track_idx = int(row_counts.argmax())
    track_row = track_idx * 2 + band_box[1] - t
    bands = int((row_counts > max(4, row_counts.max() * 0.35)).sum())
    lo, hi = max(0, track_idx - 6), min(mask.shape[0], track_idx + 7)
    sub = mask[lo:hi, :]
    col_counts = sub.sum(axis=0)
    heavy = np.where(col_counts >= 5)[0]
    content = (int(heavy.min()) * 2 + l, int(heavy.max()) * 2 + l) if heavy.size else None
    bright = np.where((small[lo:hi, :, :].min(axis=2) > 185).sum(axis=0) >= 3)[0]
    playhead = int(bright.max()) * 2 + l if bright.size else None
    out.update({"ms_scan": round((time.perf_counter() - t2) * 1000, 2),
                "track_row": track_row, "band": [lo, hi], "轨道带数": bands,
                "content": content, "playhead_x": playhead})
    if content and duration:
        pps = (content[1] - content[0]) / duration
        out["px_per_sec"] = round(pps, 1)
        if playhead:
            out["playhead_sec"] = round((playhead - content[0]) / pps, 2)
    out["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="毫秒级状态识别")
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--duration", type=float, help="成片总时长，用于把播放头像素换算成秒")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repeat", type=int, default=0, help="重复 N 次看耗时分布")
    args = ap.parse_args()

    if args.repeat:
        times = []
        last = {}
        for _ in range(args.repeat):
            last = see(args.pid, args.duration)
            times.append(last["ms"])
        s = sorted(times)
        print(f"识别 {args.repeat} 次：最小 {s[0]:.2f}｜中位 {s[len(s)//2]:.2f}｜"
              f"p95 {s[int(len(s)*0.95)]:.2f}｜最大 {s[-1]:.2f} ms")
        print(json.dumps(last, ensure_ascii=False))
        return 0

    r = see(args.pid, args.duration)
    if args.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(f"页面={r.get('page')}｜窗口={r.get('window')}｜视频轨行={r.get('track_row')}"
              f"｜内容={r.get('content')}｜播放头x={r.get('playhead_x')}"
              f"｜播放头≈{r.get('playhead_sec')}s｜合计 {r.get('ms')} ms"
              f"（窗口{r.get('ms_window')} 截图{r.get('ms_shot')} 扫描{r.get('ms_scan')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
