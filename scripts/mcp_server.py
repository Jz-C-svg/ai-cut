#!/usr/bin/env python3
r"""aicut MCP Server 启动入口（stdio 传输）。

启动方式：
  "<project-root>\venv\Scripts\python.exe" "<project-root>\scripts\mcp_server.py"
  "<project-root>\venv\Scripts\python.exe" "<project-root>\scripts\mcp_server.py" --transport stdio

日志走 stderr，stdout 只跑 JSON-RPC，不污染协议通道。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402

cli.setup()

from aicut.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
