#!/usr/bin/env python3
r"""标定鼠标键盘的速度与等待：用实测数字替代拍脑袋的常数。

测什么（都不点按钮，不按键）：
  前台判定 / 窗口缓存 / 页面状态 / 鼠标移动三档 / 区域截图 / 抢焦点
再顺手测"翻页后界面多久稳定"（--with-page-keys，会真的打开草稿再返回，默认不跑）。

用法:
  python tune_speed.py                       # 只测安全项，打印表格
  python tune_speed.py --n 50 --write        # 测完按结果写 config/ui_speed.json
  python tune_speed.py --with-page-keys      # 额外测翻页稳定时间（会动剪映）
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from aicut import cli, speed, ui  # noqa: E402

import ui_fast as F  # noqa: E402

cli.setup()


def bench(fn, n: int) -> dict:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    pick = lambda q: round(times[min(int(len(times) * q), len(times) - 1)], 3)  # noqa: E731
    return {"中位": pick(0.5), "p95": pick(0.95), "最小": round(times[0], 3) if times else 0,
            "最大": round(times[-1], 3) if times else 0, "n": len(times)}


def main() -> int:
    ap = argparse.ArgumentParser(description="界面速度标定")
    ap.add_argument("--n", type=int, default=30, help="每项测几次")
    ap.add_argument("--write", action="store_true", help="把推荐值写进 config/ui_speed.json")
    ap.add_argument("--with-page-keys", action="store_true",
                    help="额外测翻页稳定时间（会真的打开草稿再返回，请确保剪映在首页）")
    ap.add_argument("--natural", action="store_true", help="用 fast 档移动（有过程动画，看起来更像人）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pid = ui.find_pid()
    if not pid:
        print("剪映没开：先启动剪映再标定（界面速度只有在真界面上量才有意义）")
        return 1
    info = ui.info(pid)
    win = F.main_window(pid)
    left, top, right, bottom = win["rect"]
    safe = (left + 40, top + 60)  # 窗口左上角空白区，移动到这里不点任何东西

    results: dict[str, dict] = {
        "前台判定": bench(F.foreground_pid, args.n),
        "主窗口(每次枚举)": bench(lambda: F.main_window(pid), max(args.n // 3, 5)),
        "主窗口(带缓存)": bench(lambda: F.cached_main_window(pid), args.n),
        "页面状态": bench(lambda: F.page_state(pid), max(args.n // 3, 5)),
        "鼠标-瞬移": bench(lambda: F.move(*safe, speed="instant"), args.n),
        "鼠标-快": bench(lambda: F.move(*safe, speed="fast"), max(args.n // 3, 5)),
        "鼠标-常规": bench(lambda: F.move(*safe, speed="normal"), max(args.n // 3, 5)),
        "抢焦点": bench(lambda: F.activate(pid), args.n),
        "区域截图": bench(lambda: F.shot(ROOT / "work" / "ui" / "bench.png", box=(left, top, left + 240, top + 140)),
                          max(args.n // 5, 3)),
    }

    accuracy = {}
    for tier in ("instant", "fast", "normal"):
        errs = []
        for _ in range(12):
            tx = left + 40 + random.randint(0, 200)
            ty = top + 60 + random.randint(0, 100)
            F.move(tx, ty, speed=tier)
            time.sleep(0.05)
            cx, cy = ui.cursor()
            errs.append(math.hypot(cx - tx, cy - ty))
        errs.sort()
        accuracy[tier] = {"中位": round(errs[len(errs) // 2], 2), "最大": round(errs[-1], 2)}

    settle = None
    if args.with_page_keys:
        if info.get("page") != "首页":
            print("提示：请先把剪映切回草稿首页，再跑 --with-page-keys")
        else:
            samples = []
            for _ in range(3):
                t0 = time.perf_counter()
                ui.open_draft("__tune__")  # 打开首页第一张卡片
                opened = time.perf_counter() - t0
                if F.page_state(pid) != "编辑页":
                    break
                t1 = time.perf_counter()
                ui.key("ctrl+alt+q", delay=0.05)
                while F.page_state(pid) != "首页" and time.perf_counter() - t1 < 15:
                    time.sleep(0.05)
                samples.append({"打开草稿": round(opened, 3),
                                "返回首页": round(time.perf_counter() - t1, 3)})
            results["翻页稳定"] = {"样本": samples, "中位": (
                round(statistics.median([s["打开草稿"] for s in samples]), 3) if samples else None)}
            settle = results["翻页稳定"]["中位"]

    rec = {
        "move_speed": ("fast" if args.natural else "instant"),
        "click_speed": "instant",
        "key_delay_s": round(max(results["抢焦点"]["p95"], results["前台判定"]["p95"]) / 1000 + 0.02, 3),
        "action_settle_s": round(results["页面状态"]["中位"] / 1000 + 0.05, 3),
        "page_settle_s": round((settle or 1.2) * 0.6, 3),
        "measured": {"落点误差": accuracy,
                     "耗时": {k: {"中位": v["中位"], "p95": v["p95"]} for k, v in results.items() if "p95" in v}},
    }
    payload = {"pid": pid, "page": info.get("page"), "n": args.n, "结果": results, "推荐": rec}

    if args.json:
        cli.print_json(payload)
    else:
        print(f"剪映 pid={pid} 在{info.get('page')} · 每项 {args.n} 次（单位 ms）")
        print(f"{'项目':<18}{'中位':>9}{'p95':>9}{'最小':>9}{'最大':>9}")
        for name, r in results.items():
            if "p95" in r and isinstance(r.get("中位"), (int, float)):
                print(f"{name:<18}{r['中位']:>9}{r['p95']:>9}{r['最小']:>9}{r['最大']:>9}")
        print("\n落点误差（移动到的位置和实际落点差多少像素）：")
        for tier, a in accuracy.items():
            print(f"  {tier:<8} 中位 {a['中位']:.2f}px  最大 {a['最大']:.2f}px")
        print("\n推荐设置：")
        for k, v in rec.items():
            print(f"  {k} = {v}")
        if "翻页稳定" in results:
            print(f"  翻页实测样本：{results['翻页稳定']['样本']}")

    if args.write:
        speed.save(rec, note=f"由 tune_speed.py 实测写入（n={args.n}）")
        print(f"\n已写入 {speed.config_path()}（旧配置备份 .bak）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
