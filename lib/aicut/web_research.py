r"""web_research · 全网双通道检索（中文源 + 外文源一起查）。

通道 A（中文/综合，需 key）：复用 research.py 的 Responses API + web_search
通道 B（外文直连，免 key）：DuckDuckGo HTML 端点，中英双查询（en 侧覆盖 GitHub/
      Reddit/StackExchange/Wikipedia 等外文来源），requests 直拉结果页解析链接

聚合去重后交给 LLM 提炼要点。任何通道失败都降级，双通道全挂才报错。
用法:
    from aicut import web_research as wr
    result = wr.search_all("剪映 关键帧 变速", lang="both", max_results=12)
    # result: {"sources": [url...], "snippets": [ {title, url, text} ], "channels": [...]}
"""
from __future__ import annotations

import re
import urllib.parse
import json
import os
import base64
import html
import time
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AIcut/1.0"}
ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = {
    "enabled": True,
    "engines": [
        "https://html.duckduckgo.com/html/",
        "https://lite.duckduckgo.com/lite/",
    ],
    "timeout_seconds": 15,
    "retries": 2,
}


def _config() -> dict:
    path = ROOT / "config" / "web_search.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(data if isinstance(data, dict) else {})
        return cfg
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def _proxies() -> dict[str, str] | None:
    """Use an explicitly enabled Windows proxy, otherwise keep direct access."""
    if not _config().get("use_windows_proxy", True):
        return None
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        return {"http": proxy, "https": proxy}
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0]).strip()
        if not enabled or not server:
            return None
        if "=" in server:
            parts = dict(item.split("=", 1) for item in server.split(";") if "=" in item)
            server = parts.get("https") or parts.get("http") or ""
        if server and "://" not in server:
            server = "http://" + server
        return {"http": server, "https": server} if server else None
    except (OSError, ValueError):
        return None


def _clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _real_url(url: str) -> str:
    url = html.unescape(url)
    match = re.search(r"[?&]u=a1([^&]+)", url)
    if match:
        try:
            decoded = base64.b64decode(match.group(1) + "==").decode("utf-8")
            if decoded.startswith("http"):
                return decoded
        except (ValueError, UnicodeDecodeError):
            pass
    match = re.search(r"[?&]uddg=([^&]+)", url)
    return urllib.parse.unquote(match.group(1)) if match else url

# 外文查询时补充的英文检索词模板（覆盖常见外文社区）
_EN_HINTS = {
    "剪映": "JianYing CapCut",
    "变速": "speed ramp",
    "关键帧": "keyframe",
    "字幕": "subtitles",
    "转场": "transitions",
    "剪辑": "video editing",
    "卡点": "beat sync",
    "口播": "talking head",
}


def _ddg(query: str, max_results: int = 8) -> list[dict]:
    """DuckDuckGo 免费公开入口。返回 [{title, url, text}]。"""
    out: list[dict] = []
    cfg = _config()
    if not cfg.get("enabled", True):
        return out
    timeout = max(3, float(cfg.get("timeout_seconds", 15)))
    retries = max(1, int(cfg.get("retries", 2)))
    deadline = time.monotonic() + min(timeout * retries * 2, 16)
    blocks_re = (
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</a>'
    )
    session = requests.Session()
    session.headers.update(UA)
    session.proxies.update(_proxies() or {})
    for engine in cfg.get("engines") or DEFAULT_CONFIG["engines"]:
        if isinstance(engine, dict):
            endpoint = str(engine.get("url") or "")
            method = str(engine.get("method") or "post").lower()
            parser = str(engine.get("parser") or "ddg")
        else:
            endpoint, method, parser = str(engine), "post", "ddg"
        if not endpoint:
            continue
        for _ in range(retries):
            if time.monotonic() >= deadline:
                return out
            try:
                request = session.get if method == "get" else session.post
                r = request(endpoint, params={"q": query} if method == "get"
                            else {"q": query, "kl": "wt-wt"}, timeout=timeout)
                r.raise_for_status()
                if parser == "bing":
                    blocks = re.findall(
                        r'<li class="b_algo".*?<h2[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>'
                        r'(.*?)</a></h2>.*?<p[^>]*>(.*?)</p>', r.text, re.S | re.I)
                else:
                    blocks = re.findall(blocks_re, r.text, re.S)
                for url, title, text in blocks[:max_results]:
                    real = _real_url(url)
                    if real.startswith("http"):
                        out.append({"title": _clean_html(title), "url": real,
                                    "text": _clean_html(text)[:300]})
                if out:
                    return out
                break
            except requests.RequestException:
                continue
    return out


def _to_en(topic: str) -> str:
    """朴素中英混合：把主题里能映射的词换成英文，再拼原词，让外文引擎也能命中。"""
    en = topic
    for zh, e in _EN_HINTS.items():
        en = en.replace(zh, f"{zh} {e}")
    return en


def search_all(topic: str, lang: str = "both", max_results: int = 12) -> dict:
    """全网检索。lang: both(默认，中文+外文) / zh / en。

    通道 A：research.py 的联网模型（有 key 时最质量）
    通道 B：DuckDuckGo 直连（免 key；lang 含 en 时中英各查一次）
    """
    sources: list[str] = []
    snippets: list[dict] = []
    channels: list[str] = []

    # ---- 通道 A ----
    try:
        from pathlib import Path as _P
        import sys as _sys
        _sys.path.insert(0, str(_P(__file__).resolve().parent.parent.parent / "scripts"))
        import research  # noqa: E402
        if research.load_key():
            from aicut import learn  # noqa: E402
            found = learn.search_web(f"搜索并总结：{topic} 的具体做法与要点", 2500)
            for u in (found.get("来源") or []):
                if u not in sources:
                    sources.append(u)
            snippets.append({"title": f"[通道A·联网模型] {topic}", "url": (found.get("来源") or [""])[0],
                             "text": str(found.get("原文") or found)[:1500]})
            channels.append("A:responses-web_search")
    except Exception:
        pass

    # ---- 通道 B ----
    if lang in ("both", "zh"):
        snippets += _ddg(topic, max_results=max_results // 2 or 4)
        channels.append("B:ddg-zh")
    if lang in ("both", "en"):
        snippets += _ddg(_to_en(topic) + " (English)", max_results=max_results // 2 or 4)
        channels.append("B:ddg-en")

    for s in snippets:
        u = s.get("url") or ""
        if u.startswith("http") and u not in sources:
            sources.append(u)

    return {"topic": topic, "sources": sources[:20], "snippets": snippets,
            "channels": channels, "count": len(snippets)}
