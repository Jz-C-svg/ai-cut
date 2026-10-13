"""
ai-editor-core - 独立于剪辑软件的智能剪辑决策核心

核心职责：
1. 理解素材（转写+镜头分析+语义解析）
2. 生成剪辑策略（删除/调整/风格化）
3. 输出跨软件可执行的计划

不依赖任何剪辑软件格式，只依赖：音频+视频+原始元数据
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from datetime import datetime


class ActionType(Enum):
    """可执行的剪辑动作类型"""
    DELETE_REGION = "delete_region"
    SPLIT = "split"
    SPEED_CHANGE = "speed_change"
    CAPTION_ADD = "caption_add"
    BROLL_ADD = "broll_add"
    TRANSITION_ADD = "transition_add"
    FILTER_APPLY = "filter_apply"
    CROP_CHANGE = "crop_change"


class SemanticLevel(Enum):
    """语义理解深度"""
    RAW = "raw"          # 纯语音转文字
    SEGMENT = "segment"   # 片段级（镜头+口误识别）
    THEME = "theme"      # 主题级（意图+情感方向）
    META = "meta"        # 元数据级（风格+目标受众+平台建议）


@dataclass
class RawSegment:
    """原始素材片段"""
    video_path: str
    audio_path: str
    start_sec: float
    end_sec: float
    duration: float
    fps: float
    width: int
    height: int
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class SpeechSegment:
    """语音片段（带ASR）"""
    raw_id: str
    start_sec: float
    length: float
    text: str
    speaker: str
    language: str
    confidence: float
    score: float = 0.0  # 质量评分（可选）
    tags: list[str] = field(default_factory=list)
    is_mispronunciation: bool = False
    has_pause: bool = False


@dataclass
class VisualSegment:
    """视觉片段（镜头分析）"""
    raw_id: str
    start_sec: float
    length: float
    score: float  # 质量/可用性
    shot_type: str  # "talking_head" / "static" / "moving" / "detail"
    camera_movement: str  # "static" / "pan" / "zoom" / "tilt" / "handheld"
    lighting_quality: float  # 0.0-1.0
    audio_quality: float  # 0.0-1.0
    broll_candidate: bool = False  # 是否适合加B-roll
    faces_detected: int = 0
    emotion: str = "neutral"
    tags: list[str] = field(default_factory=list)


@dataclass
class Transcript:
    """完整转写结果"""
    segments: list[SpeechSegment] = field(default_factory=list)
    total_duration: float = 0.0
    languages: list[str] = field(default_factory=list)
    confidence_score: float = 0.0


@dataclass
class EditingDecision:
    """核心决策：AI生成的剪辑操作计划"""

    # 目标信息
    target_software: str  # "premiere" / "capcut" / "finalcut" 等
    target_resolution: str  # "1080p" / "4K" / "720p"
    target_aspect_ratio: str  # "16:9" / "9:16" / "1:1"

    # 剪辑策略
    strategy: str  # "concise" / "comprehensive" / "creative" / "template"

    # 具体动作列表
    actions: list[dict[str, Any]] = field(default_factory=list)

    # 推荐的风格配置
    style_config: dict[str, Any] = field(default_factory=dict)

    # 风格建议
    style_recommendations: list[str] = field(default_factory=list)

    # 平台推荐
    platform_recommendations: list[str] = field(default_factory=list)

    # 统计信息
    statistics: dict[str, Any] = field(default_factory=dict)

    # 元数据
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    input_source: str = ""
    version: str = "1.0"


@dataclass
class StyleTemplate:
    """风格模板"""
    id: str
    name: str
    description: str
    theme: str  # "education" / "entertainment" / "documentary" / "promo" / "social"
    rhythm: str  # "fast" / "medium" / "slow"

    # 通用规则
    camera_movement: list[str]
    transition_style: str
    caption_style: str
    broll_usage: bool
    caption_frequency: float  # 0.0-1.0

    # 剪辑规则
    keep_duration_min: float
    keep_duration_max: float
    delete_pause: bool
    delete_mispronunciation: bool
    speed_optimization: str  # "natural" / "compressed" / "energetic"

    # 视觉规则
    filter_appearance: bool
    color_tone: str  # "warm" / "cool" / "neutral" / "dramatic"

    # 字幕规则
    caption_position: str  # "top" / "bottom" / "floating" / "mixed"
    caption_style: dict[str, Any]

    # 适用软件
    supported_software: list[str]

    # 优先级
    priority: int = 50


@dataclass
class PlatformConfig:
    """平台配置"""
    platform: str  # "tiktok" / "youtube" / "shorts" / "bilibili" / "instagram"

    # 推荐时长
    recommended_duration: list[float]  # [30, 60, 120, 240]

    # 标题风格
    title_style: str  # "clickbait" / "descriptive" / "informative"

    # 封面建议
    cover_style: str

    # 音乐建议
    music_style: str

    # CTA建议
    cta_style: str

    # 其他规则
    other_rules: dict[str, Any] = field(default_factory=dict)


# 导出所有核心类
__all__ = [
    "ActionType",
    "SemanticLevel",
    "RawSegment",
    "SpeechSegment",
    "VisualSegment",
    "Transcript",
    "EditingDecision",
    "StyleTemplate",
    "PlatformConfig",
]
