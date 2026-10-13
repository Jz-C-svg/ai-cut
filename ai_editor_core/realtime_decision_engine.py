"""
ai-editor-core - 实时剪辑建议生成器

功能：
1. 结合视觉分析和AI决策生成实时建议
2. 实时标记可删除片段
3. 动态调整剪辑策略
4. 实时质量监控
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, List
from pathlib import Path
import time
import json
from .core import EditingDecision, ActionType, SpeechSegment
from .realtime_visual_analyzer import FrameAnalysis, AnalysisResult, RealtimeVisualAnalyzer
from .engine import DecisionEngine


@dataclass
class RealtimeSuggestion:
    """实时建议"""
    suggestion_type: str  # "delete" / "keep" / "split" / "optimize"
    timestamp: float
    position_sec: float
    duration: float

    # 详细信息
    reason: str
    confidence: float  # 0-1
    priority: str  # "high" / "medium" / "low"
    frame_analyses: List[FrameAnalysis] = field(default_factory=list)
    audio_analysis: Optional[dict[str, Any]] = None

    # 元数据
    tags: List[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RealtimeDecision:
    """实时决策"""
    current_frame: int
    current_timestamp: float

    # 剪辑状态
    target_segments: List[dict[str, Any]] = field(default_factory=list)
    delete_regions: List[dict[str, Any]] = field(default_factory=list)
    split_points: List[float] = field(default_factory=list)

    # 策略状态
    current_strategy: str
    style_config: dict[str, Any]
    quality_threshold: float

    # 建议列表
    suggestions: List[RealtimeSuggestion] = field(default_factory=list)

    # 统计
    total_decisions: int = 0
    total_suggestions: int = 0
    last_decision: Optional[RealtimeSuggestion] = None

    # 回调
    callback: Optional[Callable[[RealtimeDecision], None]] = None


class RealtimeDecisionEngine:
    """实时决策引擎"""

    def __init__(self, video_source: Any = None):
        """
        初始化实时决策引擎

        参数:
            video_source: 视频源（摄像头或视频文件）
        """
        self.visual_analyzer = RealtimeVisualAnalyzer()
        self.decision_engine = DecisionEngine()

        self.set_video_source(video_source)

        # 配置
        self.min_quality_threshold = 0.6
        self.motion_sensitivity = 0.5
        self.face_sensitivity = 0.7
        self.debounce_interval = 0.5  # 防抖动时间（秒）

        # 实时状态
        self._suggestions: List[RealtimeSuggestion] = []
        self._decisions: List[RealtimeDecision] = []
        self._frame_buffer: deque[FrameAnalysis] = deque(maxlen=10)
        self._last_decision_time: float = 0.0
        self._is_processing = False

        # 回调
        self._suggestion_callback: Optional[Callable[[RealtimeSuggestion], None]] = None
        self._decision_callback: Optional[Callable[[RealtimeDecision], None]] = None
        self._quality_alert_callback: Optional[Callable[[float], None]] = None

    def set_video_source(self, video_source: Any) -> None:
        """设置视频源"""
        self.visual_analyzer.set_video_source(video_source)

    def set_strategy(self, style_id: str, target_software: str = "premiere") -> None:
        """设置剪辑策略"""
        self.current_style_id = style_id
        self.current_target_software = target_software

    def add_suggestion_callback(self, callback: Callable[[RealtimeSuggestion], None]) -> None:
        """添加建议回调"""
        self._suggestion_callback = callback

    def add_decision_callback(self, callback: Callable[[RealtimeDecision], None]) -> None:
        """添加决策回调"""
        self._decision_callback = callback

    def add_quality_alert_callback(self, callback: Callable[[float], None]) -> None:
        """添加质量警报回调"""
        self._quality_alert_callback = callback

    def start(self) -> bool:
        """启动实时决策"""
        success = self.visual_analyzer.start()
        if success:
            logger.info("实时决策引擎已启动")
        return success

    def stop(self) -> None:
        """停止实时决策"""
        self.visual_analyzer.stop()
        logger.info("实时决策引擎已停止")

    def update(self) -> Optional[RealtimeDecision]:
        """更新并生成实时决策"""

        # 更新视觉分析
        analysis = self.visual_analyzer.update()
        if analysis is None:
            return None

        # 添加到帧缓冲区
        self._frame_buffer.append(analysis)

        # 防抖动：避免过于频繁的决策
        current_time = time.time()
        if current_time - self._last_decision_time < self.debounce_interval:
            return None

        # 生成建议
        suggestions = self._generate_suggestions(analysis)
        self._suggestions.extend(suggestions)

        # 生成决策
        decision = self._generate_realtime_decision(analysis, suggestions)

        # 更新状态
        self._decisions.append(decision)
        self._last_decision_time = current_time
        self._is_processing = False

        # 执行回调
        if self._suggestion_callback:
            for suggestion in suggestions[-5:]:  # 只回调最近5条
                self._suggestion_callback(suggestion)

        if self._decision_callback:
            self._decision_callback(decision)

        return decision

    def _generate_suggestions(self, analysis: FrameAnalysis) -> List[RealtimeSuggestion]:
        """生成实时建议"""

        suggestions = []
        suggestion_type = "keep"

        # 1. 质量相关建议
        if analysis.avg_quality < self.min_quality_threshold:
            suggestion = RealtimeSuggestion(
                suggestion_type="optimize",
                timestamp=analysis.timestamp,
                position_sec=analysis.timestamp,
                duration=5.0,  # 建议优化时长5秒
                reason=f"画面质量不足（清晰度: {analysis.sharpness_score:.2f}, 噪点: {analysis.noise_level:.2f}）",
                confidence=min(1.0, (self.min_quality_threshold - analysis.avg_quality) * 1.5),
                priority="high",
                frame_analyses=[analysis]
            )
            suggestions.append(suggestion)

        # 2. 运动相关建议
        if analysis.motion_level > self.motion_sensitivity:
            suggestion = RealtimeSuggestion(
                suggestion_type="optimize",
                timestamp=analysis.timestamp,
                position_sec=analysis.timestamp,
                duration=3.0,
                reason=f"画面运动过剧（运动度: {analysis.motion_level:.2f}）",
                confidence=min(1.0, (analysis.motion_level - self.motion_sensitivity) * 1.5),
                priority="medium",
                frame_analyses=[analysis]
            )
            suggestions.append(suggestion)

        # 3. 场景相关建议
        if analysis.scene_type == "talking_head" and not analysis.broll_frames > 5:
            suggestion = RealtimeSuggestion(
                suggestion_type="keep",
                timestamp=analysis.timestamp,
                position_sec=analysis.timestamp,
                duration=10.0,
                reason=f"适合口播场景，继续保持",
                confidence=0.8,
                priority="low",
                frame_analyses=[analysis]
            )
            suggestions.append(suggestion)

        # 4. 人脸相关建议
        if analysis.face_detected and analysis.face_count == 1:
            suggestion = RealtimeSuggestion(
                suggestion_type="keep",
                timestamp=analysis.timestamp,
                position_sec=analysis.timestamp,
                duration=8.0,
                reason=f"单人口播场景，保持焦点",
                confidence=0.9,
                priority="low",
                frame_analyses=[analysis]
            )
            suggestions.append(suggestion)

        # 5. 灯光建议
        if analysis.brightness_score < 0.3:
            suggestion = RealtimeSuggestion(
                suggestion_type="optimize",
                timestamp=analysis.timestamp,
                position_sec=analysis.timestamp,
                duration=4.0,
                reason=f"画面较暗（亮度: {analysis.brightness_score:.2f}）",
                confidence=0.75,
                priority="medium",
                frame_analyses=[analysis]
            )
            suggestions.append(suggestion)

        self._suggestions = [s for s in self._suggestions[-20:]]  # 保持最近20条

        return suggestions

    def _generate_realtime_decision(self,
                                   analysis: FrameAnalysis,
                                   suggestions: List[RealtimeSuggestion]) -> RealtimeDecision:

        decision = RealtimeDecision(
            current_frame=self.visual_analyzer._frame_number,
            current_timestamp=analysis.timestamp,
            current_strategy=analysis.scene_type,
            style_config={
                "color_tone": "neutral",
                "transition_style": "fade",
                "caption_position": "bottom"
            },
            quality_threshold=analysis.avg_quality
        )

        # 确定要删除的区域
        high_priority_suggestions = [s for s in suggestions if s.priority == "high"]

        for suggestion in suggestions[:3]:  # 最多考虑最近3条建议
            if suggestion.suggestion_type == "optimize" and suggestion.priority == "high":
                decision.delete_regions.append({
                    "start": suggestion.position_sec,
                    "end": suggestion.position_sec + suggestion.duration,
                    "reason": suggestion.reason
                })

        # 确定切分点
        if len(suggestions) > 1:
            sorted_points = sorted([s.position_sec for s in suggestions if s.suggestion_type == "keep"])
            decision.split_points = sorted_points[-3:]  # 最近3个切分点

        # 更新统计
        decision.total_decisions = len(self._decisions)
        decision.total_suggestions = len(suggestions)
        decision.last_decision = suggestions[-1] if suggestions else None

        return decision

    def get_active_suggestions(self) -> List[RealtimeSuggestion]:
        """获取当前激活的建议"""
        return self._suggestions.copy()

    def get_decision_history(self, limit: int = 100) -> List[RealtimeDecision]:
        """获取决策历史"""
        return self._decisions[-limit:] if limit > 0 else self._decisions.copy()

    def generate_final_decision(self) -> Optional[EditingDecision]:
        """生成最终剪辑决策"""

        if not self._decisions:
            return None

        # 聚合最近的决策
        latest_decision = self._decisions[-1]

        # 创建完整的决策对象
        final_decision = EditingDecision(
            target_software=self.current_target_software,
            target_resolution="1080p",
            target_aspect_ratio="16:9",
            strategy=latest_decision.current_strategy,
            style_config={
                "color_tone": "neutral",
                "transition_style": latest_decision.style_config.get("transition_style", "fade")
            },
            actions=[]
        )

        # 添加所有删除操作
        for region in latest_decision.delete_regions:
            final_decision.actions.append({
                "type": "delete_region",
                "start_sec": region["start"],
                "end_sec": region["end"],
                "reason": region["reason"]
            })

        # 添加所有切分点
        for split_point in latest_decision.split_points:
            final_decision.actions.append({
                "type": "split",
                "position": split_point,
                "context": "实时识别的语义切点"
            })

        # 更新统计
        stats = self.visual_analyzer.get_statistics()
        final_decision.statistics = {
            "total_frames": stats.total_frames,
            "avg_quality": stats.avg_quality,
            "low_quality_frames": stats.low_quality_frames,
            "motion_frames": stats.motion_frames,
            "face_frames": stats.face_frames,
        }

        final_decision.input_source = "realtime_analysis"

        return final_decision

    def export_suggestions(self, output_path: str | Path) -> Path:
        """导出建议为JSON文件"""

        suggestions_data = [
            {
                "timestamp": s.timestamp,
                "position_sec": s.position_sec,
                "duration": s.duration,
                "suggestion_type": s.suggestion_type,
                "reason": s.reason,
                "confidence": s.confidence,
                "priority": s.priority,
                "tags": s.tags
            }
            for s in self._suggestions
        ]

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        output_path.write_text(
            json.dumps({
                "suggestions": suggestions_data,
                "total_count": len(suggestions_data)
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        return output_path

    def get_statistics(self) -> dict[str, Any]:
        """获取统计信息"""

        stats = self.visual_analyzer.get_statistics()

        return {
            "total_frames": stats.total_frames,
            "avg_quality": stats.avg_quality,
            "fps": stats.fps,
            "low_quality_frames": stats.low_quality_frames,
            "motion_frames": stats.motion_frames,
            "face_frames": stats.face_frames,
            "broll_frames": stats.broll_frames,
            "total_decisions": stats.total_frames,
            "total_suggestions": len(self._suggestions),
            "current_strategy": latest_decision.current_strategy if hasattr(self, "latest_decision") else "unknown"
        }


# 导出
__all__ = [
    "RealtimeSuggestion",
    "RealtimeDecision",
    "RealtimeDecisionEngine"
]
