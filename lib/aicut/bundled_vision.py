"""Unified caller for the vision model shipped with AIcut.

The module owns discovery, health checks and structured calls so callers do not
fall back to an unrelated Ollama text model by accident.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from . import glm_deep


def info() -> dict[str, Any]:
    return {
        "id": "glm-4.6v-flash",
        "runner": glm_deep.LLAMA_CLI,
        "model": glm_deep.MODEL,
        "mmproj": glm_deep.mmproj(),
        "available": is_available(),
    }


def is_available() -> bool:
    return glm_deep.is_available()


def health() -> dict[str, Any]:
    data = info()
    if not data["available"]:
        missing = [key for key in ("runner", "model", "mmproj")
                   if not Path(str(data[key])).exists()]
        data["error"] = "missing bundled vision files: " + ", ".join(missing)
    return data


def _require() -> None:
    if not is_available():
        status = health()
        raise RuntimeError(status.get("error", "bundled vision model unavailable"))


def analyze_frame(image: str | Path, prompt: str, **kwargs: Any) -> dict[str, Any]:
    _require()
    result = glm_deep.analyze_frame(image, prompt=prompt, **kwargs)
    return _validate(result)


def analyze_video(video: str | Path, prompt: str, **kwargs: Any) -> dict[str, Any]:
    _require()
    result = glm_deep.analyze_video(video, prompt=prompt, **kwargs)
    return normalize_video_result(_validate(result))


def analyze_image_bytes(data: bytes, prompt: str, **kwargs: Any) -> dict[str, Any]:
    _require()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
        handle.write(data)
        path = Path(handle.name)
    try:
        return analyze_frame(path, prompt, **kwargs)
    finally:
        path.unlink(missing_ok=True)


def _validate(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise RuntimeError("bundled vision returned a non-object result")
    if result.get("error"):
        raise RuntimeError(f"bundled vision call failed: {result['error']}")
    payload = {str(k): v for k, v in result.items() if not str(k).startswith("_")}
    if not payload or all(str(k).strip("?") == "" for k in payload):
        raise RuntimeError("bundled vision returned unusable JSON fields")
    return result


def normalize_video_result(result: dict[str, Any]) -> dict[str, Any]:
    """Map model aliases into the planner schema without inventing observations."""
    aliases = {
        "内容": ("内容", "描述", "变化描述", "content", "description"),
        "主体": ("主体", "人物", "对象", "subject", "person"),
        "场景": ("场景", "环境", "scene", "setting"),
        "动作": ("动作", "行为", "action", "activity"),
        "类型": ("类型", "视频类型", "type", "video_type"),
        "干净度": ("干净度", "画面干净度", "画质", "cleanliness", "quality"),
        "剪辑建议": ("剪辑建议", "建议画面", "editing_suggestion", "edit_suggestion"),
    }
    out = dict(result)
    for target, keys in aliases.items():
        if target in out and str(out[target]).strip("? ") :
            continue
        for key in keys:
            value = out.get(key)
            if value is not None and str(value).strip("? "):
                out[target] = value
                break
    return out


def json_text(result: dict[str, Any]) -> str:
    return json.dumps(_validate(result), ensure_ascii=False)
