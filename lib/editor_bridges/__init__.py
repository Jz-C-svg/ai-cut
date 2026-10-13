r"""editor_bridges · 多剪辑软件隔离适配器包。

为每个剪辑软件提供独立的检测/读写/操作适配层，互不耦合。
统一入口：editor_bridges.registry

用法：
    from editor_bridges.registry import get_bridge, detect_all, selftest_all

    # 检测所有软件
    results = detect_all()

    # 获取剪映适配器
    jy = get_bridge("jianying")
    proj = jy.read("path/to/draft_content.json")
    jy.write(proj, "path/to/output.json")
    jy.control("cut")

    # 全适配器自检
    reports = selftest_all()
"""
from .base import (
    EditorBridge,
    EditorProject,
    EditorTrack,
    EditorSegment,
    DetectionResult,
    ControlResult,
    ValidationResult,
)
from . import registry

__version__ = "1.0.0"
__all__ = [
    "EditorBridge",
    "EditorProject",
    "EditorTrack",
    "EditorSegment",
    "DetectionResult",
    "ControlResult",
    "ValidationResult",
    "registry",
    "__version__",
]
