r"""界面元素记忆：定位成功一次就存下来，下次直接复用。

真相文件：config/ui_elements.json
  { "应用": { "剪映专业版": { "元素": { 名字: {偏移,锚点,证据,命中,更新时间} }, "基准尺寸": [宽,高] } } }

锚点语义：
  "左上"  窗口左上角 + 偏移
  "右上"  窗口右上角 + 偏移（偏移的 x 用负数表示向左）
  "居中"  窗口中心 + 偏移
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .paths import CONFIG_DIR

DEFAULT_APP = "剪映专业版"


def path() -> Path:
    return CONFIG_DIR / "ui_elements.json"


def _read() -> dict:
    p = path()
    if not p.exists():
        return {"说明": "界面元素记忆", "应用": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {"说明": "界面元素记忆", "应用": {}}


def _write(data: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.with_suffix(".json.bak").write_text(p.read_text(encoding="utf-8-sig"), encoding="utf-8")
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def app_block(data: dict, app: str) -> dict:
    return data.setdefault("应用", {}).setdefault(app, {"元素": {}, "基准尺寸": [1168, 780]})


def load(app: str = DEFAULT_APP) -> dict[str, dict]:
    """{元素名: {偏移,锚点,证据,命中,更新时间,基准尺寸}}"""
    data = _read()
    block = app_block(data, app)
    base = tuple(block.get("基准尺寸") or (1168, 780))
    out: dict[str, dict] = {}
    for name, entry in (block.get("元素") or {}).items():
        if isinstance(entry, dict):
            out[str(name)] = {**entry, "基准尺寸": list(base)}
    return out


def names(app: str = DEFAULT_APP) -> list[str]:
    return sorted(load(app).keys())


def base_size(app: str = DEFAULT_APP) -> tuple[int, int]:
    data = _read()
    block = app_block(data, app)
    size = block.get("基准尺寸") or (1168, 780)
    return int(size[0]), int(size[1])


def resolve(name: str, rect: tuple[int, int, int, int], app: str = DEFAULT_APP) -> tuple[int, int]:
    """把元素记忆换算成屏幕绝对坐标（窗口矩形 + 锚点 + 偏移）。"""
    entry = load(app).get(name)
    if not entry:
        raise KeyError(f"没有记住元素：{name}")
    left, top, right, bottom = rect
    anchor = str(entry.get("锚点") or "左上")
    dx, dy = (entry.get("偏移") or [0, 0])[:2]
    if anchor == "右上":
        return int(right + dx), int(top + dy)
    if anchor == "居中":
        return int((left + right) / 2 + dx), int((top + bottom) / 2 + dy)
    return int(left + dx), int(top + dy)


def remember(name: str, offset: tuple[int, int], anchor: str = "左上", evidence: str = "",
             app: str = DEFAULT_APP) -> dict:
    data = _read()
    block = app_block(data, app)
    entry = {
        "偏移": [int(offset[0]), int(offset[1])],
        "锚点": anchor,
        "证据": evidence,
        "命中": int((block.get("元素", {}).get(name) or {}).get("命中", 0)),
        "更新时间": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    block.setdefault("元素", {})[name] = entry
    _write(data)
    return entry


def touch(name: str, ok: bool = True, app: str = DEFAULT_APP) -> dict:
    """记一次命中（复用成功）/ 未命中，用于判断这条记忆可不可信。"""
    data = _read()
    block = app_block(data, app)
    entry = (block.get("元素") or {}).get(name)
    if not entry:
        return {}
    entry["命中"] = int(entry.get("命中", 0)) + (1 if ok else 0)
    if not ok:
        entry["失败"] = int(entry.get("失败", 0)) + 1
    entry["更新时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write(data)
    return entry


def locate(text: str, app: str = DEFAULT_APP) -> str | None:
    """在一段步骤文字里找出能定位的元素名（最长匹配优先）。"""
    hits = [name for name in names(app) if name and name in text]
    return max(hits, key=len) if hits else None
