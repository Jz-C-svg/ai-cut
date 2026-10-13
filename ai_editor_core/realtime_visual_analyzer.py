"""
ai-editor-core - 实时视觉分析引擎

功能：
1. 实时帧级视觉分析
2. 镜头质量检测
3. 画面元素识别（人物/场景/动作）
4. 实时剪辑建议生成
5. 低质画面警报
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional
from pathlib import Path
import time
import cv2
import numpy as np
from collections import deque
from threading import Lock
import logging


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("VisualAnalyzer")


class AnalysisResult(Enum):
    """分析结果类型"""
    QUALITY_OK = "quality_ok"
    QUALITY_LOW = "quality_low"
    MOTION_HIGH = "motion_high"
    MOTION_LOW = "motion_low"
    FACE_DETECTED = "face_detected"
    TEXT_DETECTED = "text_detected"
    BROLL_SUITABLE = "broll_suitable"
    FACE_CHANGE = "face_change"


@dataclass
class FrameAnalysis:
    """单帧分析结果"""
    frame_number: int
    timestamp: float
    width: int
    height: int

    # 质量指标
    sharpness_score: float  # 清晰度评分 0-1
    brightness_score: float  # 亮度评分 0-1
    color_balance: dict[str, float]  # 色平衡 [r, g, b]
    noise_level: float  # 噪点水平 0-1

    # 动态指标
    motion_level: float  # 运动程度 0-1
    face_detected: bool
    face_count: int
    face_position: tuple[int, int, int, int]  # (x, y, w, h)

    # 场景指标
    scene_type: str  # "talking_head" / "static" / "moving" / "environment"
    dominant_color: tuple[int, int, int]  # 主色调 RGB
    lighting_quality: float  # 0-1
    audio_level: float  # 声音电平 dB

    # 上下文
    previous_analysis: Optional[FrameAnalysis] = None
    confidence_score: float = 0.0
    tags: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    analysis_result: AnalysisResult = AnalysisResult.QUALITY_OK

    @property
    def avg_quality(self) -> float:
        """综合质量评分"""
        base_quality = (self.sharpness_score + self.brightness_score) / 2 * (1 - self.noise_level)
        return min(1.0, max(0.0, base_quality * (1 + self.confidence_score)))


@dataclass
class SessionStatistics:
    """会话统计"""
    total_frames: int = 0
    total_analysis_time: float = 0.0
    avg_quality: float = 0.0
    low_quality_frames: int = 0
    motion_frames: int = 0
    face_frames: int = 0
    broll_frames: int = 0
    last_frame_analysis: Optional[FrameAnalysis] = None
    start_time: float = field(default_factory=time.time)
    current_session_duration: float = 0.0

    @property
    def fps(self) -> float:
        """实时帧率"""
        if self.current_session_duration > 0:
            return self.total_frames / self.current_session_duration
        return 0.0

    @property
    def quality_distribution(self) -> dict[str, int]:
        """质量分布"""
        if self.avg_quality >= 0.8:
            return {"high": 1, "medium": 0, "low": 0}
        elif self.avg_quality >= 0.6:
            return {"high": 0, "medium": 1, "low": 0}
        else:
            return {"high": 0, "medium": 0, "low": 1}


class RealtimeVisualAnalyzer:
    """实时视觉分析器"""

    # 默认检测器初始化
    _face_cascade = None
    _text_detector = None
    _color_bins = [
        ((0, 0, 0), (10, 10, 10)),  # black
        ((90, 40, 40), (130, 100, 100)),  # red
        ((30, 80, 30), (80, 180, 80)),  # green
        ((20, 20, 120), (70, 70, 180)),  # blue
        ((80, 60, 60), (150, 120, 120)),  # orange
        ((100, 80, 100), (160, 140, 160)),  # purple
    ]

    def __init__(self,
                 target_width: int = 1920,
                 target_height: int = 1080,
                 min_sharpness: float = 0.6,
                 max_noise: float = 0.3,
                 analysis_interval_ms: int = 33):
        """
        初始化实时分析器

        参数:
            target_width: 目标宽度
            target_height: 目标高度
            min_sharpness: 最小清晰度阈值
            max_noise: 最大噪点阈值
            analysis_interval_ms: 分析间隔（毫秒）
        """
        self.target_width = target_width
        self.target_height = target_height
        self.min_sharpness = min_sharpness
        self.max_noise = max_noise
        self.analysis_interval = analysis_interval_ms

        self._lock = Lock()
        self._current_frame: Optional[np.ndarray] = None
        self._frame_number = 0
        self._is_running = False
        self._current_session: SessionStatistics = SessionStatistics()

        self._callbacks: list[Callable[[FrameAnalysis], None]] = []
        self._quality_callbacks: list[Callable[[float], None]] = []
        self._face_callbacks: list[Callable[[bool, int], None]] = []
        self._suggestion_callbacks: list[Callable[[str], None]] = []

    def add_callback(self, callback: Callable[[FrameAnalysis], None]) -> None:
        """添加分析结果回调"""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def add_quality_callback(self, callback: Callable[[float], None]) -> None:
        """添加质量回调"""
        if callback not in self._quality_callbacks:
            self._quality_callbacks.append(callback)

    def add_face_callback(self, callback: Callable[[bool, int], None]) -> None:
        """添加面部回调"""
        if callback not in self._face_callbacks:
            self._face_callbacks.append(callback)

    def add_suggestion_callback(self, callback: Callable[[str], None]) -> None:
        """添加建议回调"""
        if callback not in self._suggestion_callbacks:
            self._suggestion_callbacks.append(callback)

    def set_video_source(self, source: Any) -> None:
        """设置视频源（摄像头或视频文件）"""
        self._video_source = source

    def start(self) -> bool:
        """启动实时分析"""
        if self._is_running:
            return True

        try:
            # 初始化OpenCV
            if isinstance(self._video_source, int):
                self._capture = cv2.VideoCapture(self._video_source)
            elif isinstance(self._video_source, str):
                self._capture = cv2.VideoCapture(self._video_source)
            else:
                raise ValueError("不支持的视频源类型")

            # 配置摄像头
            success, self._current_frame = self._capture.read()
            if not success:
                return False

            self._is_running = True
            self._current_session.start_time = time.time()
            logger.info(f"开始实时分析，分辨率: {self._current_frame.shape}")

            return True
        except Exception as e:
            logger.error(f"启动失败: {e}")
            return False

    def stop(self) -> None:
        """停止实时分析"""
        self._is_running = False

        if hasattr(self, '_capture'):
            self._capture.release()

        logger.info(f"实时分析已停止，总帧数: {self._current_session.total_frames}")

    def update(self) -> Optional[FrameAnalysis]:
        """更新并分析下一帧"""
        if not self._is_running:
            return None

        start_time = time.time()

        # 读取帧
        success, frame = self._capture.read()
        if not success:
            self._is_running = False
            return None

        # 缩放以匹配目标分辨率
        if frame.shape[1] != self.target_width or frame.shape[0] != self.target_height:
            frame = cv2.resize(frame, (self.target_width, self.target_height))

        self._current_frame = frame
        self._frame_number += 1

        # 分析帧
        analysis = self._analyze_frame(frame)

        # 更新统计
        with self._lock:
            self._current_session.total_frames += 1
            self._current_session.last_frame_analysis = analysis
            self._current_session.current_session_duration = time.time() - self._current_session.start_time

            # 累计统计
            self._current_session.total_analysis_time += (time.time() - start_time)
            self._current_session.low_quality_frames += 1 if analysis.avg_quality < self.min_sharpness else 0
            self._current_session.motion_frames += 1 if analysis.motion_level > 0.5 else 0
            self._current_session.face_frames += 1 if analysis.face_detected else 0
            self._current_session.broll_frames += 1 if analysis.scene_type == "talking_head" else 0

        # 计算平均质量
        self._current_session.avg_quality = (
            self._current_session.avg_quality * (self._current_session.total_frames - 1) +
            analysis.avg_quality
        ) / self._current_session.total_frames if self._current_session.total_frames > 1 else analysis.avg_quality

        # 触发回调
        self._trigger_callbacks(analysis)

        return analysis

    def _analyze_frame(self, frame: np.ndarray) -> FrameAnalysis:
        """分析单帧"""
        frame_number = self._frame_number
        timestamp = time.time()

        # 1. 基础质量分析
        sharpness = self._calculate_sharpness(frame)
        brightness = self._calculate_brightness(frame)
        noise = self._calculate_noise(frame)
        avg_rgb = self._calculate_average_color(frame)

        # 2. 颜色平衡
        color_balance = self._calculate_color_balance(frame, avg_rgb)

        # 3. 动态分析
        motion_level = 0.0
        scene_type = "static"

        if frame_number > 0:
            prev_frame = self._previous_frame if hasattr(self, '_previous_frame') else frame
            motion_level = self._calculate_motion(frame, prev_frame)

            # 场景类型判定
            if motion_level < 0.1:
                scene_type = "static"
            elif motion_level < 0.3:
                scene_type = "talking_head"
            elif motion_level < 0.6:
                scene_type = "moving"
            else:
                scene_type = "environment"

        # 4. 画面元素检测
        face_detected = False
        face_count = 0
        face_position = None

        if self._face_cascade is None:
            self._face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

        try:
            gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self._face_cascade.detectMultiScale(
                gray_frame,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(30, 30)
            )

            face_count = len(faces)
            face_detected = face_count > 0

            if face_count > 0:
                # 找到最大的面部
                max_area = 0
                for i, (x, y, w, h) in enumerate(faces):
                    if w * h > max_area:
                        max_area = w * h
                        face_position = (int(x), int(y), int(w), int(h))
        except Exception:
            pass

        # 5. 灯光质量
        lighting_quality = self._calculate_lighting_quality(frame)

        # 6. 音频级别（如果有音频输入）
        audio_level = self._calculate_audio_level(frame) if hasattr(self, '_audio_source') else 0.0

        # 7. 生成建议
        suggestions = self._generate_suggestions(analysis_result)

        # 8. 结果判定
        if sharpness < self.min_sharpness:
            analysis_result = AnalysisResult.QUALITY_LOW
        elif motion_level > 0.7:
            analysis_result = AnalysisResult.MOTION_HIGH
        elif face_detected:
            analysis_result = AnalysisResult.FACE_DETECTED
        else:
            analysis_result = AnalysisResult.QUALITY_OK

        # 创建分析对象
        analysis = FrameAnalysis(
            frame_number=frame_number,
            timestamp=timestamp,
            width=frame.shape[1],
            height=frame.shape[0],
            sharpness_score=sharpness,
            brightness_score=brightness,
            color_balance=color_balance,
            noise_level=noise,
            motion_level=motion_level,
            face_detected=face_detected,
            face_count=face_count,
            face_position=face_position,
            scene_type=scene_type,
            dominant_color=avg_rgb,
            lighting_quality=lighting_quality,
            audio_level=audio_level,
            analysis_result=analysis_result
        )

        # 保存上一帧用于下一帧比较
        self._previous_frame = frame.copy()

        return analysis

    def _calculate_sharpness(self, frame: np.ndarray) -> float:
        """计算清晰度（拉普拉斯算子）"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        sharpness = np.var(laplacian)
        return min(1.0, sharpness / 1000.0)

    def _calculate_brightness(self, frame: np.ndarray) -> float:
        """计算平均亮度"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        avg_brightness = np.mean(gray)
        return min(1.0, max(0.0, avg_brightness / 255.0))

    def _calculate_noise(self, frame: np.ndarray) -> float:
        """计算噪点水平"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        diff = cv2.GaussianBlur(gray, (0, 0), 1.0)
        std_dev = cv2.compareHist(
            cv2.calcHist([gray], [0], None, [256], [0, 30]),
            cv2.calcHist([gray], [0], None, [256], [0, 30]),
            cv2.HISTCMP_BHATTACHARYYA
        )
        return min(1.0, std_dev / 10.0)

    def _calculate_motion(self, frame: np.ndarray, prev_frame: np.ndarray) -> float:
        """计算运动程度"""
        if prev_frame is None:
            return 0.0

        diff = cv2.absdiff(prev_frame.astype(np.int16), frame.astype(np.int16))
        diff = diff.astype(np.int16)

        motion_mask = np.sum(np.abs(diff) > 30, axis=2) > 0

        motion_ratio = np.sum(motion_mask) / motion_mask.size
        return min(1.0, motion_ratio)

    def _calculate_color_balance(self, frame: np.ndarray, avg_rgb: tuple) -> dict[str, float]:
        """计算颜色平衡"""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # 根据主导颜色确定平衡
        r, g, b = avg_rgb

        if abs(r - g) > abs(r - b) and abs(r - g) > 20:
            balance = {"r": 1.05, "g": 0.95, "b": 0.95}
        elif abs(g - r) > abs(g - b) and abs(g - r) > 20:
            balance = {"r": 0.95, "g": 1.05, "b": 0.95}
        elif abs(b - r) > abs(b - g) and abs(b - r) > 20:
            balance = {"r": 0.95, "g": 0.95, "b": 1.05}
        else:
            balance = {"r": 1.0, "g": 1.0, "b": 1.0}

        return balance

    def _calculate_average_color(self, frame: np.ndarray) -> tuple[int, int, int]:
        """计算主色调"""
        average_color = (
            int(np.mean(frame[:, :, 2])),
            int(np.mean(frame[:, :, 1])),
            int(np.mean(frame[:, :, 0]))
        )
        return average_color

    def _calculate_lighting_quality(self, frame: np.ndarray) -> float:
        """计算灯光质量"""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        v_channel = hsv[:, :, 2]
        std_dev = np.std(v_channel)
        return min(1.0, std_dev / 50.0)

    def _calculate_audio_level(self, frame: np.ndarray) -> float:
        """计算音频级别"""
        # 注意：这里需要实际音频源，帧分析无法直接获取
        # 返回模拟值
        return 0.0

    def _generate_suggestions(self, analysis: FrameAnalysis) -> list[str]:
        """生成剪辑建议"""
        suggestions = []

        # 清晰度建议
        if analysis.sharpness_score < self.min_sharpness:
            suggestions.append("画面清晰度不足，建议调整相机或镜头")

        # 亮度建议
        if analysis.brightness_score < 0.4:
            suggestions.append("画面太暗，建议增加光线")

        # 噪点建议
        if analysis.noise_level > self.max_noise * 1.5:
            suggestions.append("画面噪点较多，建议降低ISO或使用更高分辨率")

        # 运动建议
        if analysis.motion_level > 0.7:
            suggestions.append("画面运动过剧，建议使用稳定器")

        # 场景类型建议
        if analysis.scene_type == "talking_head":
            suggestions.append("适合口播场景，考虑添加B-roll丰富内容")

        # 面部检测建议
        if analysis.face_detected and analysis.face_count > 1:
            suggestions.append("检测到多人场景，注意说话人的焦点")

        # 色调建议
        if analysis.dominant_color in [(0, 0, 0), (255, 255, 255)]:
            suggestions.append("背景色对比度不足，建议更换背景")

        return suggestions[:5]  # 返回最多5条建议

    def _trigger_callbacks(self, analysis: FrameAnalysis) -> None:
        """触发所有回调"""
        for callback in self._callbacks:
            try:
                callback(analysis)
            except Exception as e:
                logger.error(f"回调执行失败: {e}")

        for quality_callback in self._quality_callbacks:
            try:
                quality_callback(analysis.avg_quality)
            except Exception as e:
                logger.error(f"质量回调执行失败: {e}")

        for face_callback in self._face_callbacks:
            try:
                face_callback(analysis.face_detected, analysis.face_count)
            except Exception as e:
                logger.error(f"面部回调执行失败: {e}")

        for suggestion_callback in self._suggestion_callbacks:
            for suggestion in analysis.suggestions[:1]:  # 每帧最多1条
                try:
                    suggestion_callback(suggestion)
                except Exception as e:
                    logger.error(f"建议回调执行失败: {e}")

    def get_statistics(self) -> SessionStatistics:
        """获取会话统计"""
        with self._lock:
            return self._current_session

    def clear_session(self) -> None:
        """清除会话统计"""
        with self._lock:
            self._current_session = SessionStatistics()
            self._frame_number = 0
            logger.info("会话统计已清除")
