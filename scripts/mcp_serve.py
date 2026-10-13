"""MCP Server 独立启动入口（供 Claude Desktop / Codex / 任意 MCP 客户端配置）。

MCP 客户端配置示例（command 指向本脚本）：
    python <project-root>\\scripts\\mcp_serve.py
协议：JSON-RPC 2.0 over stdio（NDJSON），零第三方依赖。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from aicut.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
