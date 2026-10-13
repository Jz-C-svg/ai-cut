#!/usr/bin/env python3
r"""常驻服务的命令行客户端：把一条指令发给 ui_daemon 并打印结果。

用法:
  python ui_cmd.py page
  python ui_cmd.py key ctrl+b
  python ui_cmd.py click 500 739
  python ui_cmd.py shot C:\...\a.png
  python ui_cmd.py --batch "key ctrl+b | sleep 300 | key delete | shot a.png"
"""
from __future__ import annotations

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8765


def send(req: dict, port: int, retries: int = 1) -> dict:
    last = None
    for _ in range(retries):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                return json.loads(buf.decode("utf-8").strip() or "{}")
        except Exception as exc:
            last = exc
            time.sleep(0.2)
    raise RuntimeError(f"连不上常驻服务（先跑 ui_daemon.py）: {last}")


def auto_pid() -> int | None:
    """没给 --pid 时，自动找剪映的进程号（谁有主窗口就用谁）。"""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import ui_fast as F
        cands = [p for p in {w["pid"] for w in F.windows()} if True]
        import subprocess as sp
        out = sp.run(["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/FO", "CSV", "/NH"],
                     capture_output=True, text=True, encoding="gbk", errors="replace")
        pids = []
        for line in (out.stdout or "").splitlines():
            cols = [c.strip('"') for c in line.split('","')]
            if len(cols) >= 2 and cols[1].isdigit():
                pids.append(int(cols[1]))
        for p in pids:
            if F.main_window(p):
                return p
        return pids[0] if pids else None
    except Exception:
        return None


def ensure_daemon(port: int, pid: int | None) -> None:
    if pid is None:
        pid = auto_pid()
    try:
        send({"cmd": "ping"}, port)
        return
    except Exception:
        pass
    py = str(ROOT / "venv" / "Scripts" / "python.exe")
    args = [py, str(ROOT / "scripts" / "ui_daemon.py"), "--port", str(port)]
    if pid:
        args += ["--pid", str(pid)]
    subprocess.Popen(args, creationflags=0x08000000,  # CREATE_NO_WINDOW
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=str(ROOT))
    for _ in range(40):
        time.sleep(0.25)
        try:
            send({"cmd": "ping"}, port)
            return
        except Exception:
            continue
    raise RuntimeError("常驻服务启动失败")


def build_req(args) -> dict:
    c = args.cmd
    if c == "page":
        return {"cmd": "page", "pid": args.pid}
    if c == "windows":
        return {"cmd": "windows", "pid": args.pid}
    if c == "activate":
        return {"cmd": "activate", "pid": args.pid}
    if c == "key":
        return {"cmd": "key", "pid": args.pid, "keys": args.rest[0]}
    if c == "click":
        return {"cmd": "click", "pid": args.pid, "x": int(args.rest[0]), "y": int(args.rest[1])}
    if c == "move":
        return {"cmd": "move", "pid": args.pid, "x": int(args.rest[0]), "y": int(args.rest[1])}
    if c == "wheel":
        return {"cmd": "wheel", "pid": args.pid, "delta": int(args.rest[0]), "ctrl": args.ctrl}
    if c == "shot":
        return {"cmd": "shot", "pid": args.pid, "out": args.rest[0]}
    if c == "mem":
        return {"cmd": "mem", "pid": args.pid, "app": args.rest[0], "name": args.rest[1]}
    return {"cmd": c, "pid": args.pid}


def run_batch(spec: str, port: int, pid: int | None) -> int:
    steps = [s.strip() for s in spec.split("|") if s.strip()]
    total = 0.0
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)   # 复用一条连接
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    f = sock.makefile("rwb")

    def call(req: dict) -> dict:
        f.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        f.flush()
        return json.loads(f.readline().decode("utf-8").strip() or "{}")

    for step in steps:
        if step.startswith("sleep"):
            ms = int(step.split()[1])
            time.sleep(ms / 1000)
            print(f"  sleep {ms}ms")
            continue
        parts = step.split()
        req = {"cmd": parts[0], "pid": pid}
        if parts[0] in ("key",):
            req["keys"] = parts[1]
        elif parts[0] in ("click", "move"):
            req["x"], req["y"] = int(parts[1]), int(parts[2])
        elif parts[0] == "wheel":
            req["delta"] = int(parts[1])
        elif parts[0] == "shot":
            req["out"] = parts[1]
        r = call(req)
        total += r.get("ms", 0)
        print(f"  {step:36s} → {r.get('ms')} ms {'' if r.get('ok') else '失败:' + str(r.get('error'))}")
    print(f"[批量完成] 共 {len(steps)} 步，服务端累计 {total:.2f} ms，平均 {total/max(1,len(steps)):.2f} ms/步")
    sock.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="常驻服务客户端")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--pid", type=int, help="目标进程；不给就用服务默认值")
    ap.add_argument("--batch", help="批量指令，用 | 分隔")
    ap.add_argument("--ctrl", action="store_true", help="wheel 时按住 Ctrl")
    ap.add_argument("--no-start", action="store_true", help="不自动启动常驻服务")
    ap.add_argument("cmd", nargs="?")
    ap.add_argument("rest", nargs="*")
    args = ap.parse_args()

    t0 = time.perf_counter()
    if not args.no_start:
        ensure_daemon(args.port, args.pid)
    boot = (time.perf_counter() - t0) * 1000

    if args.batch:
        print(f"[客户端] 启动/连接常驻服务 {boot:.0f} ms（仅首次）")
        return run_batch(args.batch, args.port, args.pid)
    if not args.cmd:
        print("用法: ui_cmd.py page|key|click|move|wheel|shot|mem ...")
        return 2
    r = send(build_req(args), args.port)
    print(json.dumps(r, ensure_ascii=False))
    print(f"[客户端] 连接+往返 {(time.perf_counter()-t0)*1000:.0f} ms（服务端 {r.get('ms')} ms）")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
