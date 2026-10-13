"""
smart_export · 注册表与统一调用入口
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import SmartExportAdapter, ExportTask, ExportResult, DetectionResult

# 软件 ID → 适配器模块路径（延迟导入）
_BRIDGE_REGISTRY: dict[str, str] = {
    "jianying": "smart_export.jianying",
    "capcut": "smart_export.capcut",
    "premiere": "smart_export.premiere",
    "davinci": "smart_export.davinci",
    "finalcut": "smart_export.finalcut",
    "vegas": "smart_export.vegas",
}

# 软件 ID → 中文显示名
_SOFTWARE_NAMES: dict[str, str] = {
    "jianying": "剪映专业版",
    "capcut": "CapCut",
    "premiere": "Adobe Premiere Pro",
    "davinci": "DaVinci Resolve",
    "finalcut": "Final Cut Pro",
    "vegas": "VEGAS Pro",
}

_instance_cache: dict[str, SmartExportAdapter] = {}


def list_software() -> list[dict[str, str]]:
    """列出所有已注册软件的 ID 和名称"""
    return [
        {"id": sid, "name": _SOFTWARE_NAMES.get(sid, sid)}
        for sid in _BRIDGE_REGISTRY
    ]


def get_adapter(software_id: str) -> SmartExportAdapter:
    """获取指定软件的适配器实例（单例缓存）"""
    sid = software_id.strip().lower()
    if sid not in _BRIDGE_REGISTRY:
        raise ValueError(
            f"未知剪辑软件: {software_id}（可选: {', '.join(_BRIDGE_REGISTRY)}）"
        )
    if sid in _instance_cache:
        return _instance_cache[sid]
    mod_path = _BRIDGE_REGISTRY[sid]
    mod = importlib.import_module(mod_path)
    bridge_cls = getattr(mod, "ADAPTER_CLASS", None)
    if bridge_cls is None:
        raise RuntimeError(f"适配器模块 {mod_path} 未定义 ADAPTER_CLASS")
    instance = bridge_cls()
    _instance_cache[sid] = instance
    return instance


def detect_all() -> list[DetectionResult]:
    """检测所有已注册软件的状态"""
    results: list[DetectionResult] = []
    for sid in _BRIDGE_REGISTRY:
        try:
            adapter = get_adapter(sid)
            detection_result = adapter.detect()
            results.append(detection_result)
        except Exception as exc:
            from .base import DetectionResult
            results.append(DetectionResult(
                software_id=sid,
                software_name=_SOFTWARE_NAMES.get(sid, sid),
                installed=False,
                running=False,
                supported_methods=[],
                available=False,
                detection_method="",
                detail=f"适配器加载失败: {exc}"
            ))
    return results


def get_available() -> list[DetectionResult]:
    """返回可用软件列表"""
    return [r for r in detect_all() if r.available]


# ============================================================
# 导出结果统计与优化（简化版）
# ============================================================

_export_history: list[dict] = []


def register_export_result(
    software_id: str,
    method: str,
    success: bool,
    duration: float,
    file_size: float
):
    """记录导出结果"""
    _export_history.append({
        "software_id": software_id,
        "method": method,
        "success": success,
        "duration": duration,
        "file_size": file_size,
        "timestamp": time.time()
    })


def get_optimal_method_stats(software_id: str, methods: list) -> list[ExportMethod]:
    """根据历史数据估算成功率"""
    history = [h for h in _export_history if h["software_id"] == software_id]

    result: dict[str, ExportMethod] = {}
    for method in methods:
        method_id = method.method_id
        method_history = [h for h in history if h["method"] == method_id]

        if not method_history:
            continue

        # 成功率 = 成功次数 / 总次数
        success_count = sum(1 for h in method_history if h["success"])
        success_rate = success_count / len(method_history)

        # 平均耗时
        avg_duration = sum(h["duration"] for h in method_history) / len(method_history)

        # 更新方法信息
        result[method_id] = ExportMethod(
            method_id=method_id,
            name=method.name,
            enabled=method.enabled,
            success_rate=success_rate,
            average_duration=avg_duration,
            description=f"基于 {len(method_history)} 次历史统计"
        )

    for method in methods:
        if method.method_id in result:
            yield result[method.method_id]
        else:
            yield method
