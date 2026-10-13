#!/usr/bin/env python3
r"""主体观测台：在浏览器里看它看到什么、打算做什么、做完的结果。

只读，不提供任何写操作；数据全部来自 memory.sqlite 与剪映窗口状态，所以主体在不在
跑都能看（看的是它留下的账）。

用法:
  python agent_console.py                    # http://127.0.0.1:8850
  python agent_console.py --port 8851 --host 0.0.0.0    # 局域网也能看
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import ui  # noqa: E402
from aicut.memory import open_memory  # noqa: E402

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>AI 剪辑主体 · 观测台</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root{color-scheme:dark}
 body{margin:0;background:#12141a;color:#e8eaf0;font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}
 header{padding:18px 24px;background:#181b23;border-bottom:1px solid #262b36;display:flex;gap:16px;align-items:baseline}
 header h1{font-size:17px;margin:0;font-weight:600}
 header .meta{color:#8b93a7;font-size:12px}
 main{padding:20px 24px;display:grid;grid-template-columns:minmax(420px,1.4fr) minmax(320px,1fr);gap:20px}
 .card{background:#181b23;border:1px solid #262b36;border-radius:10px;padding:14px 16px}
 .card h2{font-size:13px;margin:0 0 10px;color:#9aa3b8;font-weight:600;letter-spacing:.04em}
 .row{display:flex;gap:10px;align-items:baseline;padding:5px 0;border-bottom:1px dashed #232833}
 .row:last-child{border-bottom:0}
 .t{color:#7d8598;font-variant-numeric:tabular-nums;min-width:62px}
 .k{color:#8ec07c;min-width:84px}
 .bad{color:#f2777a}.good{color:#8ec07c}.warn{color:#fabd2f}
 .note{color:#c9cfdd;flex:1;word-break:break-word}
 .pill{background:#20242e;border-radius:999px;padding:1px 9px;font-size:12px;color:#aeb6c8}
 ol{margin:0;padding-left:18px}
 @media(max-width:900px){main{grid-template-columns:1fr}}
</style></head>
<body>
<header>
  <h1>AI 剪辑主体 · 观测台</h1>
  <span class="meta" id="clock"></span>
  <span class="meta" id="jy"></span>
</header>
<main>
  <div>
    <div class="card"><h2>任务</h2><div id="tasks">加载中…</div></div>
    <div class="card" style="margin-top:20px"><h2>步骤回放（最新任务）</h2><div id="steps">加载中…</div></div>
  </div>
  <div>
    <div class="card"><h2>技能成功率</h2><div id="skills">…</div></div>
    <div class="card" style="margin-top:20px"><h2>它记住的偏好</h2><div id="prefs">…</div></div>
    <div class="card" style="margin-top:20px"><h2>快照（可回滚）</h2><div id="snaps">…</div></div>
  </div>
</main>
<script>
function esc(s){return (s==null?"":String(s)).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function fmt(ts){const d=new Date(ts*1000);return d.toTimeString().slice(0,8);}
async function tick(){
  try{
    const r=await fetch("/api/state");const s=await r.json();
    document.getElementById("clock").textContent="更新于 "+new Date().toLocaleTimeString();
    const u=s.ui||{};
    document.getElementById("jy").textContent=u.running?("剪映 "+u.page+" pid "+u.pid):"剪映未运行";
    document.getElementById("tasks").innerHTML=(s.tasks||[]).map(t=>
      `<div class="row"><span class="t">${esc(t.task)}</span>
       <span class="pill">${t.n} 步</span>
       <span class="${t.fails?"bad":"good"}">${t.fails?("失败 "+t.fails):"全通过"}</span></div>`).join("")||"<div class='row'>还没有任务</div>";
    const st=(s.steps||[]).slice().reverse();
    document.getElementById("steps").innerHTML=st.map(e=>{
      const d=e.detail||{};const ok=e.ok===null?"":(e.ok?'<span class="good">OK</span>':'<span class="bad">FAIL</span>');
      const dur=(d.duration&&d.duration.length===2)?`${d.duration[0]}→${d.duration[1]}s`:"";
      return `<div class="row"><span class="t">${fmt(e.created)}</span><span class="k">${esc(e.kind)}</span>
        ${ok}<span class="note">${esc(e.goal)} ${esc(d.note||"")} <span class="warn">${esc(dur)}</span></span></div>`;}).join("")||"<div class='row'>无</div>";
    document.getElementById("skills").innerHTML=(s.skills||[]).map(k=>
      `<div class="row"><span class="note">${esc(k.id)}</span><span class="pill">${esc(k.channel)}</span>
       <span class="${k.success>=0.8?"good":(k.success>0?"warn":"bad")}">${Math.round((k.success||0)*100)}%</span>
       <span class="meta">${k.runs} 次</span></div>`).join("")||"<div class='row'>还没跑过技能</div>";
    document.getElementById("prefs").innerHTML=(s.preferences||[]).map(p=>
      `<div class="row"><span class="pill">${Math.round(p.confidence*100)}%</span><span class="note">${esc(p.rule)}</span></div>`).join("")||"<div class='row'>还没学到你的习惯</div>";
    document.getElementById("snaps").innerHTML=(s.snapshots||[]).slice(0,8).map(x=>
      `<div class="row"><span class="t">${fmt(x.created)}</span><span class="note">${esc(x.draft)} · ${esc(x.note)}</span>
       <span class="pill">${esc(x.id)}</span></div>`).join("")||"<div class='row'>无</div>";
  }catch(e){document.getElementById("clock").textContent="读不到状态："+e;}
}
tick();setInterval(tick,3000);
</script>
</body></html>
"""


def build_state() -> dict:
    mem = open_memory()
    tasks = mem.tasks(limit=12)
    latest = tasks[0]["task"] if tasks else None
    steps = mem.episodes(latest, limit=40) if latest else []
    try:
        jy = ui.info()
    except Exception as exc:  # noqa: BLE001
        jy = {"running": False, "error": str(exc)[:100]}
    return {
        "generated": time.time(),
        "ui": jy,
        "tasks": tasks,
        "latest_task": latest,
        "steps": steps,
        "skills": mem.skills(),
        "preferences": mem.preferences(limit=12),
        "snapshots": mem.snapshots(limit=12),
        "stats": mem.stats(),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            body = json.dumps(build_state(), ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
        elif path.startswith("/api/task/"):
            task = path.rsplit("/", 1)[-1]
            mem = open_memory()
            body = json.dumps({"task": task, "episodes": mem.episodes(task, limit=200)},
                              ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
        elif path == "/health":
            self._send(b"ok", "text/plain")
        else:
            self._send(b"not found", "text/plain", 404)

    def log_message(self, *args) -> None:  # 静音
        return


def main() -> int:
    ap = argparse.ArgumentParser(description="主体观测台")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8850)
    ap.add_argument("--once", action="store_true", help="渲染一次状态就退出（自检用）")
    args = ap.parse_args()

    if args.once:
        state = build_state()
        print(json.dumps({k: (v if k in ("generated", "latest_task", "stats") else len(v) if isinstance(v, (list, dict)) else v)
                          for k, v in state.items()}, ensure_ascii=False, indent=2))
        return 0

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"观测台已启动：http://{args.host}:{args.port}   （Ctrl+C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
