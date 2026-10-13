#!/usr/bin/env python3
r"""调参层自测：识别灵敏度、静音安全阀、界面速度参数。

重点回归两件事：
  1. 纯画面/无有效声音的素材**绝不能**被当成"全是停顿"整片剪掉（安全阀）
  2. 灵敏度档位之间的差异必须单调（越敏感 → 判糊的镜头不会更少）

用法:
  python selftest_tuning.py
  python selftest_tuning.py --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, perception, skills, speed, ui, vision  # noqa: E402
from aicut.paths import ffmpeg  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def make_silent(path: Path) -> bool:
    if path.exists():
        return True
    proc = subprocess.run(
        [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=4",
         "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-shortest",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return path.exists() and proc.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="调参层自测")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    work = ROOT / "work" / "selftest"
    work.mkdir(parents=True, exist_ok=True)

    # 1. 灵敏度配置
    original = perception.load()
    cuts = {name: perception.apply_preset(name)["scene_cut"] for name in ("high", "medium", "low")}
    check("灵敏度三档可用且单调", cuts["high"] < cuts["medium"] < cuts["low"], json.dumps(cuts))
    blurs = {name: perception.apply_preset(name)["blur_sensitivity"] for name in ("high", "medium", "low")}
    check("越敏感的档模糊权重越大", blurs["high"] > blurs["medium"] > blurs["low"], json.dumps(blurs))
    perception.apply_preset(original.get("preset", "medium"))
    check("配置能落盘", perception.save(note="selftest").exists(), str(perception.config_path()))
    perception._CACHE = original  # noqa: SLF001

    # 2. 静音安全阀（危险 bug 回归）
    silent = work / "silent.mp4"
    if make_silent(silent):
        spans = skills.detect_silences(silent, -30.0, 0.5)
        guarded = bool(skills.LAST_SILENCE_INFO.get("guard"))
        check("纯画面/无有效声音不被当停顿", spans == [] and guarded,
              f"spans={len(spans)} guard={guarded}")
    else:
        check("纯画面/无有效声音不被当停顿", False, "造不出测试素材")

    speech = ROOT / "work" / "normalized" / "月子知识-0299.mp4"
    if speech.exists():
        spans = skills.detect_silences(speech, -30.0, 0.5)
        check("有语音的素材仍能抓到停顿", len(spans) >= 1,
              "、".join(f"{a:.2f}-{b:.2f}s" for a, b in spans))
    else:
        check("有语音的素材仍能抓到停顿", False, "缺少 0299 素材")

    # 3. 灵敏度差异是真实可测的
    if speech.exists():
        counts = {}
        for name in ("low", "high"):
            perception.apply_preset(name)
            stream = vision.analyze(speech, use_cache=False)
            counts[name] = sum(1 for s in stream["shots"] if s["grade"] == "糊")
        check("灵敏度差异可测（高≥低）", counts["high"] >= counts["low"], json.dumps(counts))
        perception._CACHE = original  # noqa: SLF001

    # 4. 界面速度参数
    sp = speed.load()
    need = ("move_speed", "click_speed", "key_delay_s", "post_move_ms", "click_press_ms",
            "page_settle_s", "open_draft_settle_s")
    missing = [k for k in need if k not in sp]
    check("界面速度参数齐全", not missing, "缺 " + "、".join(missing) if missing else
          f"移动={sp['move_speed']} 键延时={sp['key_delay_s']}s 点击={sp['post_move_ms']}+{sp['click_press_ms']}ms")
    check("点击时序为正数", float(sp["post_move_ms"]) > 0 and float(sp["click_press_ms"]) > 0,
          f"{sp['post_move_ms']}/{sp['click_press_ms']} ms")
    check("DPI 感知已内置", callable(getattr(ui, "_set_dpi_awareness", None)))
    cur = ui.cursor()
    check("能读光标位置", isinstance(cur, tuple) and len(cur) == 2 and all(isinstance(v, int) for v in cur),
          str(cur))
    check("速度配置有实测记录", bool(sp.get("measured")),
          f"{len(sp.get('measured', {}).get('耗时', {}))} 项耗时记录")

    perception._CACHE = original  # noqa: SLF001
    failed = [r for r in RESULTS if not r[1]]
    if args.json:
        cli.print_json({"results": [{"name": n, "ok": o, "detail": d} for n, o, d in RESULTS],
                        "fail": len(failed)})
        return 1 if failed else 0
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
