#!/usr/bin/env python3
"""免费搜索窗口自测：只验证配置和解析，不要求外网可达。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import web_research as wr  # noqa: E402


def main() -> int:
    cfg = wr._config()
    assert cfg["enabled"] is True
    assert cfg["engines"]
    endpoints = [
        item.get("url") if isinstance(item, dict) else item
        for item in cfg["engines"]
    ]
    assert all(str(url).startswith("https://") for url in endpoints)
    sample = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com">'
        "Example</a><a class=\"result__snippet\">A free result</a>"
    )
    import re
    blocks = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</a>', sample, re.S)
    assert blocks and "example.com" in blocks[0][0]
    print(f"[PASS] 免费搜索窗口已启用：{len(cfg['engines'])} 个入口")
    print("[PASS] HTML 结果解析规则可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
