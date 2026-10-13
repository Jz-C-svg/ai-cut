"""
smart_export · 智能导出适配器基础模块
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import SmartExportRegistry


@dataclass
class ExportTask:
    """导出任务统一表示"""
    software_id: str
    project_name: str
    output_dir: str = "."  # 字符串路径
    format: str = "mp4"  # mp4, mov, png, etc.
    quality: str = "high"  # high, medium, low
    settings: dict = field(default_factory=dict)  # 分辨率、帧率、码率等

    # 内部使用（适配器内部）
    method: str = ""  # "ui", "mcp", "project"

    def __post_init__(self):
        # 确保 output_dir 是 Path 对象
        if isinstance(self.output_dir, str):
            from pathlib import Path
            self.output_dir = Path(self.output_dir)


@dataclass
class ExportResult:
    """导出结果"""
    success: bool
    output_path: Path
    duration: float  # 秒
    file_size_mb: float
    method: str  # "ui", "mcp", "project"
    error_msg: str | None = None
    metadata: dict = field(default_factory=dict)  # 软件特定数据


@dataclass
class ExportMethod:
    """导出方式定义"""
    method_id: str  # "ui", "mcp", "project"
    name: str
    enabled: bool = True  # 是否启用
    success_rate: float = 1.0  # 成功率（0-1）
    average_duration: float = 0.0  # 平均耗时（秒）
    description: str = ""


@dataclass
class DetectionResult:
    """软件检测结果"""
    software_id: str
    software_name: str
    installed: bool
    running: bool
    supported_methods: list[ExportMethod]
    available: bool
    detection_method: str
    detail: str = ""


# ============================================================
# 抽象基类
# ============================================================


class SmartExportAdapter(ABC):
    """智能导出适配器基类"""

    # 软件信息（由子类实现）
    SOFTWARE_ID: str = ""
    SOFTWARE_NAME: str = ""
    SUPPORTED_FORMATS: list[str] = ["mp4", "mov"]
    DEFAULT_QUALITY: str = "high"

    def __init__(self):
        self._registry: SmartExportRegistry | None = None

    @property
    def registry(self) -> SmartExportRegistry:
        """获取注册表实例"""
        if self._registry is None:
            from .registry import SmartExportRegistry
            self._registry = SmartExportRegistry()
        return self._registry

    # ============================================================
    # 抽象方法（必须由子类实现）
    # ============================================================

    @abstractmethod
    def detect(self) -> DetectionResult:
        """检测软件安装/运行状态"""
        pass

    @abstractmethod
    def detect_export_methods(self) -> list[ExportMethod]:
        """检测支持的导出方式"""
        pass

    @abstractmethod
    def choose_optimal_method(
        self,
        task: ExportTask,
        methods: list[ExportMethod]
    ) -> ExportMethod:
        """选择最优导出方式"""
        pass

    @abstractmethod
    def export_via_ui(
        self,
        project_name: str,
        settings: dict
    ) -> ExportResult:
        """通过 UI 自动化导出"""
        pass

    @abstractmethod
    def export_via_mcp(
        self,
        project_name: str,
        settings: dict
    ) -> ExportResult:
        """通过 MCP 协议导出"""
        pass

    @abstractmethod
    def export_via_project(
        self,
        project_name: str,
        output_path: Path,
        settings: dict
    ) -> ExportResult:
        """通过工程文件解析导出"""
        pass

    # ============================================================
    # 通用方法（子类可复用）
    # ============================================================

    def get_export_path(self, task: ExportTask) -> Path:
        """生成导出路径"""
        output_path = task.output_dir / f"{task.project_name}.{task.format}"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path

    def log_result(self, method: str, result: ExportResult):
        """记录导出结果（用于后续优化）"""
        self.registry.register_export_result(
            software_id=self.SOFTWARE_ID,
            method=method,
            success=result.success,
            duration=result.duration,
            file_size=result.file_size_mb
        )

    def estimate_settings(self, task: ExportTask) -> dict:
        """根据质量参数预估导出设置"""
        quality_map = {
            "high": {"crf": 18, "preset": "medium", "bitrate": "10 Mbps"},
            "medium": {"crf": 23, "preset": "fast", "bitrate": "5 Mbps"},
            "low": {"crf": 28, "preset": "ultrafast", "bitrate": "2 Mbps"},
        }
        return quality_map.get(task.quality, quality_map["medium"]).copy()


@dataclass
class ControlResult:
    """控制操作结果"""
    success: bool
    message: str
    detail: str = ""
