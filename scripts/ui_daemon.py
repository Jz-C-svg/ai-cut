#!/usr/bin/env python3
r"""常驻指令服务：一次启动，之后每条指令都是毫秒级响应。

为什么需要它：每次新起 python 进程要 300-600ms（导入 + COM 初始化），
而真正的动作只要 1-5ms。把服务常驻，指令延迟就从"几百毫秒"变成"个位数毫秒"。

协议：TCP 127.0.0.1:<port>，一行一个 JSON 请求，返回一行 JSON 结果（含耗时 ms）。
启动：python ui_daemon.py --port 8765            （后台常驻）
客户端：python ui_cmd.py key ctrl+b

命令：ping / page / windows / activate / key / click / move / wheel / shot / mem / learn
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

DEFAULT_PORT = 8765
STATE: dict = {"pid": None}


def handle(req: dict) -> dict:
    import ui_fast as F

    t0 = time.perf_counter()
    cmd = req.get("cmd")
    pid = int(req.get("pid") or STATE["pid"] or 0)
    if cmd != "pid" and pid:
        # 剪映重启后进程号会变：发现旧号失联就自动跟随新进程
        if F.main_window(pid) is None:
            import subprocess
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe",
                                "/FO", "CSV", "/NH"], capture_output=True, text=True,
                               encoding="gbk", errors="replace")
            cands = []
            for line in (r.stdout or "").splitlines():
                cols = [c.strip('"') for c in line.split('","')]
                if len(cols) >= 2 and cols[1].isdigit():
                    cands.append(int(cols[1]))
            for p in cands:
                if F.main_window(p) is not None:
                    STATE["pid"] = pid = p
                    break
    out: dict = {"ok": True, "cmd": cmd}
    out["pid"] = pid

    if cmd == "ping":
        out["pong"] = True
    elif cmd == "pid":
        STATE["pid"] = pid
        out["pid"] = pid
    elif cmd == "page":
        out["page"] = F.page_state(pid)
    elif cmd == "windows":
        out["windows"] = [{"pid": w["pid"], "class": w["class"], "title": w["title"],
                           "w": w["width"], "h": w["height"]} for w in F.windows(pid or None)]
    elif cmd == "activate":
        F.activate(pid)
        out["foreground"] = F.foreground_pid()
        out["focused"] = out["foreground"] == pid
    elif cmd == "key":
        F.activate(pid)
        if F.foreground_pid() != pid:
            out = {"ok": False, "cmd": cmd, "error": f"拿不到焦点（前台 {F.foreground_pid()}）"}
        else:
            out["ms_send"] = round(F.send(req["keys"]), 2)
    elif cmd == "click":
        F.activate(pid)
        out["ms_click"] = round(F.click(int(req["x"]), int(req["y"]),
                                        req.get("speed", "instant"),
                                        guard_pid=pid if req.get("guard", True) else None), 2)
    elif cmd == "move":
        out["ms_move"] = round(F.move(int(req["x"]), int(req["y"]), req.get("speed", "instant")), 2)
    elif cmd == "wheel":
        out["ms_wheel"] = round(F.wheel(int(req.get("delta", 3)), bool(req.get("ctrl"))), 2)
    elif cmd == "shot":
        out["ms_shot"] = round(F.shot(req["out"], tuple(req["box"]) if req.get("box") else None), 2)
        out["path"] = req["out"]
    elif cmd == "mem":
        import ui_agent as ua
        if req.get("set"):
            ua.mem_put(req["app"], req["name"], tuple(req["offset"]), tuple(req.get("size", (1920, 1032))),
                       req.get("anchor", "左上"), req.get("evidence", ""))
            out["stored"] = req["name"]
        else:
            x, y, info = ua.locate(req["app"], req["name"], pid or None)
            out.update({"x": x, "y": y, "source": info["来源"]})
    elif cmd == "quit":
        out["bye"] = True
        threading.Thread(target=lambda: (time.sleep(0.2), raise_exit())).start()
    else:
        out = {"ok": False, "error": f"未知命令 {cmd}"}

    out["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out


def raise_exit():
    import os
    os._exit(0)


def serve(port: int, pid: int | None) -> None:
    if pid:
        STATE["pid"] = pid
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # 关掉 Nagle，消掉 40ms 级抖动
    srv.bind(("127.0.0.1", port))
    srv.listen(8)
    print(f"[常驻] 监听 127.0.0.1:{port}，默认 pid={pid}", flush=True)
    while True:
        conn, _addr = srv.accept()
        with conn:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            buf = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        req = json.loads(line.decode("utf-8"))
                        resp = handle(req)
                    except Exception as exc:
                        resp = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="常驻界面指令服务")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--pid", type=int, help="默认目标进程（剪映）")
    args = ap.parse_args()

    # 预热：把 UIA/PIL 都加载好，之后每条指令都省掉初始化
    import ui_fast as F
    t0 = time.perf_counter()
    _ = F.windows()
    if args.pid:
        _ = F.page_state(args.pid)
    print(f"[预热] {int((time.perf_counter()-t0)*1000)} ms", flush=True)
    serve(args.port, args.pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
