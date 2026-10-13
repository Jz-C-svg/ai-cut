#!/usr/bin/env python3
r"""体检：一次把主体能不能干活需要的条件全查一遍。

用法:
  python doctor.py
  python doctor.py --json
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, llm, ui  # noqa: E402
from aicut.ollama_endpoint import ollama_url, ollama_base_url
from aicut.memory import open_memory  # noqa: E402
from aicut.paths import CONTENT_LIBRARY, DATA, PROJECT_DRAFTS, WORK, draft_root, ffmpeg, python_exe  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, str, str]] = []


def add(name: str, level: str, detail: str) -> None:
    RESULTS.append((name, level, detail))
    icon = {"OK": "OK  ", "WARN": "WARN", "FAIL": "FAIL"}[level]
    print(f"[{icon}] {name} · {detail}")


def check_python() -> None:
    exe = Path(python_exe())
    add("Python 环境", "OK" if exe.exists() else "FAIL",
        f"{sys.version.split()[0]} · {exe}" if exe.exists() else f"找不到 {exe}")


def check_ffmpeg() -> None:
    for tool in (ffmpeg(), "ffprobe"):
        path = shutil.which(tool)
        if not path:
            add(f"{tool}", "FAIL", "不在 PATH 里；抽帧/探测时长会失败")
            continue
        try:
            out = subprocess.run([tool, "-version"], capture_output=True, text=True, timeout=20)
            first = (out.stdout or "").splitlines()[0][:48] if out.stdout else ""
            add(tool, "OK", f"{path} · {first}")
        except Exception as exc:  # noqa: BLE001
            add(tool, "FAIL", f"{type(exc).__name__}: {exc}")


def check_llm() -> None:
    key = llm.api_key()
    add("DeepSeek Key", "OK" if key else "WARN",
        "已配置（规划用 deepseek-chat）" if key else "没有 DEEPSEEK_API_KEY，规划会退回本地模型/规则")
    try:
        with urllib.request.urlopen(ollama_url('/api/tags'), timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models", [])]
        add("本地 Ollama", "OK" if names else "WARN", f"{ollama_base_url()}，{len(names)} 个模型" + (f"，含 {names[0]}" if names else "，需检查服务的模型目录"))
    except Exception as exc:  # noqa: BLE001
        add("本地 Ollama", "WARN", f"连不上 {ollama_base_url()}（{type(exc).__name__}），请检查本地模型服务")


def check_jianying() -> None:
    info = ui.info()
    if info.get("running"):
        add("剪映", "OK", f"在{info['page']}（pid {info['pid']}，窗口 {info['width']}x{info['height']}）")
    else:
        add("剪映", "WARN", "没开：界面通道技能（花字/云字体/导出/打开草稿）要开了才能用")


def check_drafts() -> None:
    root = draft_root()
    add("剪映草稿目录", "OK" if root.exists() else "WARN", str(root))
    if PROJECT_DRAFTS.exists():
        names = [d.name for d in PROJECT_DRAFTS.iterdir() if (d / "draft_content.json").exists()]
        add("工程草稿", "OK" if names else "WARN", f"06-drafts 里 {len(names)} 个：" + "、".join(names[:4]))
    else:
        add("工程草稿", "FAIL", "06-drafts 不存在")


def check_memory() -> None:
    mem = open_memory()
    stats = mem.stats()
    add("记忆库", "OK" if Path(stats["db"]).exists() else "WARN",
        f"episodes {stats['episodes']} · skills {stats['skills']} · preferences {stats['preferences']} · "
        f"snapshots {stats['snapshots']} · visual {stats['visual_events']}")
    if stats["preferences"] == 0:
        add("学习你的习惯", "WARN", "还没跑过 learn_from_demo.py，规划器没有偏好可依")
    else:
        add("学习你的习惯", "OK", f"已记住 {stats['preferences']} 条偏好")


def check_library() -> None:
    for name in ("styles.json", "hooks.json", "structures.json", "topics.json"):
        p = CONTENT_LIBRARY / name
        if not p.exists():
            add(f"内容库 {name}", "WARN", "缺失")
    if all((CONTENT_LIBRARY / n).exists() for n in ("styles.json", "hooks.json")):
        add("内容库", "OK", str(CONTENT_LIBRARY))


def check_disk() -> None:
    try:
        usage = shutil.disk_usage(str(ROOT))
        free_gb = usage.free / (1024 ** 3)
        level = "OK" if free_gb > 10 else "WARN"
        add("磁盘空间", level, f"{ROOT.drive} 剩余 {free_gb:.1f} GB（快照和成片都要地方）")
    except Exception as exc:  # noqa: BLE001
        add("磁盘空间", "WARN", str(exc)[:60])


def check_ports() -> None:
    for port, what in ((8848, "网页工作台"), (8765, "毫秒级指令服务")):
        s = socket.socket()
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            add(f"端口 {port}", "OK", f"{what} 在跑")
        except OSError:
            add(f"端口 {port}", "OK", f"{what} 没跑（需要时客户端会自动拉起）")
        finally:
            s.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="环境体检")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.json:
        print(f"AI cut 体检 · {ROOT}\n")
    check_python()
    check_ffmpeg()
    check_llm()
    check_jianying()
    check_drafts()
    check_memory()
    check_library()
    check_disk()
    check_ports()

    fails = [r for r in RESULTS if r[1] == "FAIL"]
    warns = [r for r in RESULTS if r[1] == "WARN"]
    if args.json:
        cli.print_json({"results": [dict(zip(("name", "level", "detail"), r)) for r in RESULTS],
                        "fail": len(fails), "warn": len(warns)})
        return 1 if fails else 0
    print(f"\n{len(RESULTS)} 项检查：失败 {len(fails)}，警告 {len(warns)}")
    if warns:
        print("警告不影响剪不了，只影响剪得好不好：" + "、".join(r[0] for r in warns))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
