r"""editor_bridges · 统一注册表与调度器。

所有适配器在此注册，外部通过 get_bridge(software_id) 获取实例。
detect_all() 并行检测所有已注册软件，返回可用性矩阵。
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from .base import EditorBridge, DetectionResult, EditorProject, ControlResult


# 软件 ID → 适配器模块路径（延迟导入，避免未安装依赖时报错）
_BRIDGE_REGISTRY: dict[str, str] = {
    "jianying": "editor_bridges.jianying",
    "capcut": "editor_bridges.capcut",
    "premiere": "editor_bridges.premiere",
    "davinci": "editor_bridges.davinci",
    "finalcut": "editor_bridges.finalcut",
    "vegas": "editor_bridges.vegas",
    "aftereffects": "editor_bridges.aftereffects",
}

# 软件 ID → 中文显示名
_SOFTWARE_NAMES: dict[str, str] = {
    "jianying": "剪映专业版",
    "capcut": "CapCut",
    "premiere": "Adobe Premiere Pro",
    "davinci": "DaVinci Resolve",
    "finalcut": "Final Cut Pro",
    "vegas": "VEGAS Pro",
    "aftereffects": "Adobe After Effects",
}

_instance_cache: dict[str, EditorBridge] = {}


def list_software() -> list[dict[str, str]]:
    """列出所有已注册软件的 ID 和名称。"""
    return [
        {"id": sid, "name": _SOFTWARE_NAMES.get(sid, sid)}
        for sid in _BRIDGE_REGISTRY
    ]


def get_bridge(software_id: str) -> EditorBridge:
    """获取指定软件的适配器实例（单例缓存）。"""
    sid = software_id.strip().lower()
    if sid not in _BRIDGE_REGISTRY:
        raise ValueError(
            f"未知剪辑软件: {software_id}（可选: {', '.join(_BRIDGE_REGISTRY)}）"
        )
    if sid in _instance_cache:
        return _instance_cache[sid]
    mod_path = _BRIDGE_REGISTRY[sid]
    mod = importlib.import_module(mod_path)
    bridge_cls = getattr(mod, "BRIDGE_CLASS", None)
    if bridge_cls is None:
        # 约定：模块里第一个 EditorBridge 子类
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, EditorBridge)
                and obj is not EditorBridge
            ):
                bridge_cls = obj
                break
    if bridge_cls is None:
        raise RuntimeError(f"适配器模块 {mod_path} 未定义 BRIDGE_CLASS 或 EditorBridge 子类")
    instance = bridge_cls()
    _instance_cache[sid] = instance
    return instance


def detect_all() -> list[DetectionResult]:
    """检测所有已注册软件的安装/运行状态。"""
    results: list[DetectionResult] = []
    for sid in _BRIDGE_REGISTRY:
        try:
            bridge = get_bridge(sid)
            results.append(bridge.detect())
        except Exception as exc:
            results.append(DetectionResult(
                software_id=sid,
                software_name=_SOFTWARE_NAMES.get(sid, sid),
                installed=False,
                running=False,
                detection_method="",
                detail=f"适配器加载失败: {exc}",
                available=False,
            ))
    return results


def detect_available() -> list[DetectionResult]:
    """返回当前可用（已安装或支持离线工程读写）的软件列表。"""
    return [r for r in detect_all() if r.available]


def read_project(software_id: str, project_path: str | Path) -> EditorProject:
    """便捷函数：读取指定软件的工程文件。"""
    return get_bridge(software_id).read(project_path)


def write_project(software_id: str, project: EditorProject, output_path: str | Path) -> Path:
    """便捷函数：写为指定软件的工程文件。"""
    return get_bridge(software_id).write(project, output_path)


def control(software_id: str, action: str, **kwargs) -> ControlResult:
    """便捷函数：执行指定软件的操作。"""
    return get_bridge(software_id).control(action, **kwargs)


def selftest_all() -> list[dict[str, Any]]:
    """对所有适配器运行自检，返回结果列表。"""
    results: list[dict[str, Any]] = []
    for sid in _BRIDGE_REGISTRY:
        try:
            bridge = get_bridge(sid)
            results.append(bridge.selftest())
        except Exception as exc:
            results.append({
                "software_id": sid,
                "software_name": _SOFTWARE_NAMES.get(sid, sid),
                "detect": None,
                "roundtrip": False,
                "validate": False,
                "errors": [f"适配器加载失败: {exc}"],
            })
    return results
