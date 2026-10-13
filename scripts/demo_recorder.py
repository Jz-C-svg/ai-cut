#!/usr/bin/env python3
r"""录你的操作：你在剪映里剪，它在旁边记——记的不是你的按键，是你的"结果"。

每 0.5 秒只看一次文件指纹（stat，几乎零开销）；真变了才读工程、留证据：
改动前 / 改动后的 draft_content.json + 剪映截图 + 结构差异。

记完可以蒸馏成规则（LLM 读这些证据，产出"你的偏好"），写进记忆库，之后主体规划
时强制遵守。这是"代替我"最直接的一条路，也最安全——学的是你自己的做法。

用法:
  python demo_recorder.py --minutes 240 --distill-every 20    # 后台长期边剪边学
  python demo_recorder.py --minutes 10 --distill               # 录完立刻蒸馏
  python demo_recorder.py --distill-only                       # 只把已录的蒸馏一遍
  python demo_recorder.py --watch "work\demo-test" --minutes 1 --interval 0.3   # 自测
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, diff as diff_mod, llm, ui  # noqa: E402
from aicut.draft import Draft  # noqa: E402
from aicut.memory import open_memory  # noqa: E402
from aicut.paths import PROJECT_DRAFTS, WORK, draft_root, fingerprint  # noqa: E402

cli.setup()

RECORDS = WORK / "demos"
SHOT = WORK / "ui" / "recorder.png"


def candidate_drafts(watch: list[Path]) -> list[Path]:
    out: dict[str, Path] = {}
    for base in watch:
        if not base.exists():
            continue
        for p in base.rglob("draft_content.json"):
            if "snapshots" in p.parts:
                continue
            out[str(p)] = p
    return list(out.values())


def record_change(path: Path, before_text: str, after_text: str, shot: Path | None,
                  index: int) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = RECORDS / f"{stamp}-{index:02d}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "before.json").write_text(before_text, encoding="utf-8")
    (out / "after.json").write_text(after_text, encoding="utf-8")
    if shot and Path(shot).exists():
        try:
            shutil.copy2(shot, out / "screen.png")
        except OSError:
            pass
    summary: list[str] = []
    try:
        a = Draft.load(out / "before.json")
        b = Draft.load(out / "after.json")
        d = diff_mod.diff(a, b)
        summary = d["summary"]
        info = {"draft": path.parent.name, "changes": d, "at": time.time()}
    except Exception as exc:  # noqa: BLE001
        info = {"draft": path.parent.name, "error": str(exc)[:200], "at": time.time()}
    (out / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"dir": str(out), "draft": path.parent.name, "summary": summary}


def distill(limit: int = 30, provider: str = "auto") -> dict:
    """把录到的证据交给模型，产出"你的偏好"。"""
    infos: list[dict] = []
    for d in sorted(RECORDS.glob("*/info.json"))[-limit:]:
        try:
            infos.append(json.loads(d.read_text(encoding="utf-8")))
        except ValueError:
            continue
    if not infos:
        return {"ok": False, "error": "还没有录制内容"}
    brief = []
    for i in infos:
        c = i.get("changes") or {}
        if not c:
            continue
        brief.append({
            "草稿": i.get("draft"),
            "时长": f"{c['duration']['before']:.2f} -> {c['duration']['after']:.2f}s",
            "位移": (c.get("uniform_shift") or {}).get("delta"),
            "删片段": len(c.get("removed") or []),
            "增片段": len(c.get("added") or []),
            "字幕去掉": (c.get("texts_removed") or [])[:3],
            "字段变化": [f"{x['field']}:{x['before']}->{x['after']}" for x in (c.get("changed") or [])][:5],
        })
    prompt = (
        "下面是一个人手工剪辑短视频时留下的一串操作结果记录（每条是一次改动的前后对比）。\n"
        "请归纳出他的**剪辑习惯**，输出 JSON：\n"
        '{"偏好":[{"规则":"一句话可执行的习惯","证据":"依据哪几条","置信度":0.5-0.9}],'
        '"观察":"一句话总述他的风格"}\n'
        "只写能从记录里推出来的，不要编。\n记录：\n" + json.dumps(brief, ensure_ascii=False)
    )
    try:
        out = llm.json_chat([{"role": "system", "content": "你是剪辑习惯分析员，只输出 JSON。"},
                             {"role": "user", "content": prompt}], provider=provider)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    mem = open_memory()
    written = []
    for item in out.get("偏好") or []:
        rule = str(item.get("规则") or "").strip()
        if not rule:
            continue
        mem.add_preference(rule, evidence=str(item.get("证据") or "录制记录")[:200],
                           confidence=float(item.get("置信度") or 0.6), source="demo")
        written.append(rule)
    return {"ok": True, "观察": out.get("观察"), "写入偏好": written, "样本": len(brief)}


def snapshot_once(video_path: Path) -> None:
    try:
        if ui.find_pid():
            ui.shot(SHOT)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="录你的操作 -> 蒸馏成偏好")
    ap.add_argument("--watch", action="append", help="额外的监视目录（默认 06-drafts 与剪映草稿目录）")
    ap.add_argument("--minutes", type=float, default=30, help="录多久")
    ap.add_argument("--interval", type=float, default=0.5, help="采样间隔秒")
    ap.add_argument("--distill", action="store_true", help="录完立刻蒸馏")
    ap.add_argument("--distill-every", type=float, default=0.0,
                    help="每隔多少分钟蒸馏一次（0=只在结束时）")
    ap.add_argument("--distill-only", action="store_true", help="只蒸馏已录内容")
    ap.add_argument("--stop-file", help="出现这个文件就优雅退出（方便后台停止）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.distill_only:
        result = distill()
        cli.print_json(result) if args.json else print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    watch = [Path(w) for w in (args.watch or [])] or [PROJECT_DRAFTS, draft_root()]
    RECORDS.mkdir(parents=True, exist_ok=True)
    print(f"开始录制：监视 {[str(w) for w in watch]}，每 {args.interval}s 只看指纹，录 {args.minutes} 分钟")
    print("（你在剪映里正常剪就行；它只读草稿文件，不记录你的键鼠输入）")

    fps: dict[str, str] = {}
    texts: dict[str, str] = {}
    for path in candidate_drafts(watch):
        try:
            fps[str(path)] = fingerprint(path)
            texts[str(path)] = path.read_text(encoding="utf-8")
        except OSError:
            pass

    deadline = time.time() + args.minutes * 60
    next_distill = time.time() + (args.distill_every * 60 if args.distill_every else 1e9)
    events: list[dict] = []
    index = 0
    stop_file = Path(args.stop_file) if args.stop_file else None
    try:
        while time.time() < deadline:
            if stop_file and stop_file.exists():
                print("检测到停止文件，优雅退出")
                break
            for path in candidate_drafts(watch):
                key = str(path)
                try:
                    fp = fingerprint(path)
                except OSError:
                    continue
                if fps.get(key) == fp:
                    continue
                fps[key] = fp
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                before = texts.get(key)
                texts[key] = text
                if before is None or before == text:
                    continue
                index += 1
                snapshot_once(path)
                ev = record_change(path, before, text, SHOT, index)
                events.append(ev)
                line = "；".join(ev["summary"][:3]) if ev["summary"] else "结构有变化"
                print(f"  [{time.strftime('%H:%M:%S')}] 记录第 {index} 次改动（{ev['draft']}）：{line}")
            if args.distill_every and time.time() >= next_distill:
                res = distill()
                print(f"  [{time.strftime('%H:%M:%S')}] 周期蒸馏：写入 {len(res.get('写入偏好') or [])} 条")
                next_distill = time.time() + args.distill_every * 60
            time.sleep(max(args.interval, 0.2))
    except KeyboardInterrupt:
        print("手动停止录制")

    print(f"录制结束：共记录 {len(events)} 次改动 -> {RECORDS}")
    if args.distill and events:
        print(json.dumps(distill(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
