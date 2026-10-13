#!/usr/bin/env python3
r"""本地 Python 客户端实测：真起子进程跑 MCP Server，走 stdio JSON-RPC 往返。

验证项（硬性验收）：
  1) initialize 握手成功
  2) tools/list 返回 >=15 个工具
  3) list_drafts 返回草稿列表（含 月子知识-成片）
  4) draft_status(月子知识-成片) 返回时长/轨道/片段数
  5) list_effects(kind=转场, keyword=模糊) 返回转场列表
  6) 写操作往返：对草稿副本调 split_at，验证 ok=True 且片段数变化

用法：
  "<project-root>\venv\Scripts\python.exe" "<project-root>\scripts\test_mcp_client.py"
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
SERVER = ROOT / "scripts" / "mcp_server.py"
DRAFTS = ROOT / "06-drafts"
SRC_DRAFT = "月子知识-成片"
TMP_DRAFT = "_mcp-test-成片"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}  {detail}", flush=True)


class Client:
    def __init__(self) -> None:
        env = {**__import__("os").environ,
               "PYTHONPATH": str(ROOT / "lib"),
               "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        self.proc = subprocess.Popen(
            [str(PYTHON), str(SERVER), "--transport", "stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True, encoding="utf-8", bufsize=1,
        )
        self._id = 0

    def _read(self, timeout: float = 60.0) -> dict:
        import threading
        out: dict = {}

        def _reader() -> None:
            line = self.proc.stdout.readline()  # type: ignore[union-attr]
            out["msg"] = json.loads(line) if line.strip() else None

        th = threading.Thread(target=_reader, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive() or "msg" not in out:
            raise TimeoutError("等待 MCP 响应超时")
        return out["msg"] or {}

    def request(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method,
               "params": params or {}}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]
        resp = self._read(timeout)
        if resp.get("id") != self._id:
            raise RuntimeError(f"响应 id 不匹配：{resp}")
        if "error" in resp:
            raise RuntimeError(f"JSON-RPC 错误：{resp['error']}")
        return resp.get("result") or {}

    def notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]

    def call_tool(self, name: str, args: dict, timeout: float = 120.0) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": args}, timeout)
        text = (result.get("content") or [{}])[0].get("text", "{}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"raw": text}
        return {"isError": result.get("isError", False), "data": data}

    def close(self) -> None:
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        err = (self.proc.stderr.read() if self.proc.stderr else "") or ""
        return err


def main() -> int:
    # 准备草稿副本（不动真实草稿）
    src = DRAFTS / SRC_DRAFT
    tmp = DRAFTS / TMP_DRAFT
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp)
    print(f"已复制测试草稿：{SRC_DRAFT} -> {TMP_DRAFT}", flush=True)

    client = Client()
    stderr_log = ""
    try:
        # 1) initialize
        init = client.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        })
        check("1. initialize 握手",
              init.get("serverInfo", {}).get("name") == "aicut-mcp"
              and "protocolVersion" in init,
              f"serverInfo={init.get('serverInfo')}")
        client.notify("notifications/initialized")

        # 2) tools/list >= 15
        tools = client.request("tools/list").get("tools", [])
        names = [t["name"] for t in tools]
        must_have = ["list_drafts", "draft_status", "set_active_draft",
                     "split_at", "add_subtitle", "list_effects",
                     "delete_silences", "generate_draft", "review_film",
                     "apply_style_card", "apply_realtime_marks",
                     "add_transition", "add_filter", "set_gain_db",
                     "add_styled_text", "add_text_animation",
                     "delete_range", "delete_head", "delete_tail", "trim_to",
                     "get_draft_summary", "validate_draft",
                     "list_draft_materials", "get_draft_timeline"]
        missing = [n for n in must_have if n not in names]
        check("2. tools/list >=15 个工具", len(tools) >= 15,
              f"共 {len(tools)} 个")
        check("2b. 关键工具齐全", not missing, f"缺失={missing}" if missing else "齐全")

        # 3) list_drafts 含月子知识-成片
        ld = client.call_tool("list_drafts", {})
        drafts = ld["data"].get("drafts", [])
        check("3. list_drafts 含月子知识-成片",
              SRC_DRAFT in drafts and TMP_DRAFT in drafts,
              f"共 {len(drafts)} 个：{drafts[:6]}...")

        # 4) draft_status
        ds = client.call_tool("draft_status", {"draft_name": SRC_DRAFT})
        d = ds["data"]
        check("4. draft_status 返回时长/轨道/片段",
              d.get("ok") and d.get("duration", 0) > 0 and d.get("track_count", 0) > 0,
              f"时长={d.get('duration')}s 轨道={d.get('tracks')} 视频段={d.get('video_segments')}")

        # 5) list_effects 转场 模糊
        le = client.call_tool("list_effects", {"kind": "转场", "keyword": "模糊"})
        eff = le["data"]
        eff_list = eff.get("items") or eff.get("effects") or eff.get("matches") or []
        check("5. list_effects(转场,模糊) 返回列表",
              eff.get("ok") is not False and (len(eff_list) > 0 or eff.get("count", 0) > 0),
              f"返回字段={list(eff.keys())}")

        # 5b) learn_template_from_draft（增强工具：逆向提取样式模板）
        lt = client.call_tool("learn_template_from_draft", {"draft_name": SRC_DRAFT})
        ltd = lt["data"]
        check("5b. learn_template_from_draft 提取文本模板",
              ltd.get("ok") and ltd.get("text_count", 0) > 0,
              f"text_count={ltd.get('text_count')} 模板={json.dumps(ltd.get('template'), ensure_ascii=False)}")

        # 5c) v3.3 P1-3：视频理解侧只读工具（get_video_info / detect_video_scenes）
        SAMPLE_VIDEO = ROOT / "work" / "normalized" / "月子知识-0299.mp4"
        if SAMPLE_VIDEO.exists():
            gvi = client.call_tool("get_video_info",
                                   {"video_path": str(SAMPLE_VIDEO)})
            gvid = gvi["data"]
            check("5c. get_video_info 返回时长/分辨率/帧率",
                  gvid.get("ok") and gvid.get("duration", 0) > 0
                  and gvid.get("size") and gvid.get("fps", 0) > 0,
                  f"{gvid.get('duration')}s {gvid.get('size')} {gvid.get('fps')}fps")
            dvs = client.call_tool("detect_video_scenes",
                                   {"video_path": str(SAMPLE_VIDEO)})
            dvsc = dvs["data"]
            check("5d. detect_video_scenes 返回场景列表",
                  dvsc.get("ok") and "scenes" in dvsc and dvsc.get("duration", 0) > 0,
                  f"scene_count={dvsc.get('scene_count')}")
        else:
            check("5c. get_video_info（无样例素材，跳过）", True, "")
            check("5d. detect_video_scenes（无样例素材，跳过）", True, "")

        # 5e) v3.4 P1：草稿只读审计/概览工具
        gds = client.call_tool("get_draft_summary", {"draft_name": SRC_DRAFT})
        gd = gds["data"]
        check("5e. get_draft_summary 返回 canvas/时长/素材库存",
              gd.get("ok") and gd.get("canvas", {}).get("width")
              and gd.get("duration_sec", 0) > 0 and "material_inventory" in gd,
              f"canvas={gd.get('canvas')} dur={gd.get('duration_sec')}s 库存={gd.get('material_inventory')}")

        vd = client.call_tool("validate_draft", {"draft_name": SRC_DRAFT})
        vdd = vd["data"]
        check("5f. validate_draft 返回问题清单结构",
              vdd.get("ok") and vdd.get("plaintext") is True
              and "issues" in vdd and "summary" in vdd,
              f"issues={vdd.get('issue_count')} summary={vdd.get('summary')} "
              f"codes={[i.get('code') for i in vdd.get('issues', [])]}")

        ldm = client.call_tool("list_draft_materials", {"draft_name": SRC_DRAFT})
        ldd = ldm["data"]
        check("5g. list_draft_materials 列出素材带 exists",
              ldd.get("ok") and ldd.get("total", 0) >= 1
              and all("exists" in m for m in ldd.get("materials", [])),
              f"total={ldd.get('total')} missing={ldd.get('missing')} "
              f"paths={[m.get('path') for m in ldd.get('materials', [])]}")

        gdt = client.call_tool("get_draft_timeline", {"draft_name": SRC_DRAFT})
        gdt_d = gdt["data"]
        first_seg_ok = (gdt_d.get("tracks") or [{}])[0].get("segments", [{}])[0]
        check("5h. get_draft_timeline 返回每轨每段 start/end",
              gdt_d.get("ok") and gdt_d.get("track_count", 0) > 0
              and all("start" in s and "end" in s
                      for tr in gdt_d.get("tracks", []) for s in tr.get("segments", [])),
              f"tracks={[(t.get('type'), t.get('segment_count')) for t in gdt_d.get('tracks', [])]}")

        # 6) 写操作往返：对副本 split_at
        before = client.call_tool("draft_status", {"draft_name": TMP_DRAFT})["data"]
        v_before = before.get("video_segments", 0)
        dur = before.get("duration", 5.0)
        t_cut = round(min(1.5, dur / 2), 3)
        sp = client.call_tool("split_at", {"draft_name": TMP_DRAFT, "t": t_cut})
        after = client.call_tool("draft_status", {"draft_name": TMP_DRAFT})["data"]
        v_after = after.get("video_segments", 0)
        check("6. split_at 写往返 ok=True",
              sp["data"].get("ok") is True,
              f"report={json.dumps(sp['data'], ensure_ascii=False)[:200]}")
        check("6b. split_at 后视频片段数变化",
              v_after >= v_before + 1,
              f"{v_before} -> {v_after}")

    finally:
        stderr_log = client.close()
        # 清理临时草稿
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n----- server stderr 尾部 -----", flush=True)
    print("\n".join((stderr_log or "").splitlines()[-15:]), flush=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n===== 结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 =====", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
