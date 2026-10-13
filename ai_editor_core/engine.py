"""
ai-editor-core - 智能决策引擎

负责将原始素材和AI理解转换为剪辑决策
"""

from .core import (
    EditingDecision,
    SpeechSegment,
    VisualSegment,
    Transcript,
    ActionType,
    StyleTemplate,
    PlatformConfig
)
import json
from pathlib import Path
from typing import Any


class DecisionEngine:
    """剪辑决策引擎"""

    def __init__(self, style_templates: list[StyleTemplate] = None, platform_configs: dict = None):
        self.style_templates = style_templates or self._default_styles()
        self.platform_configs = platform_configs or self._default_platforms()

    def _default_styles(self) -> list[StyleTemplate]:
        """默认风格模板"""
        return [
            StyleTemplate(
                id="koubo-zhishi",
                name="口播知识",
                description="清晰专业的知识分享，适合教育类内容",
                theme="education",
                rhythm="medium",
                camera_movement=["static"],
                transition_style="fade",
                caption_style={"color": "#ffffff", "bg_color": "#00000080", "size": "20"},
                broll_usage=True,
                caption_frequency=0.8,
                keep_duration_min=3.0,
                keep_duration_max=120.0,
                delete_pause=True,
                delete_mispronunciation=True,
                speed_optimization="natural",
                filter_appearance=True,
                color_tone="neutral",
                caption_position="bottom",
                caption_style={"color": "#ffffff", "bg_color": "#00000080", "font_size": 24},
                supported_software=["premiere", "capcut", "finalcut"],
                priority=100
            ),
            StyleTemplate(
                id="space-showcase",
                name="空间展示",
                description="展示空间环境，适合旅游、家居、装修",
                theme="documentary",
                rhythm="slow",
                camera_movement=["pan", "tilt", "zoom"],
                transition_style="dissolve",
                caption_style={"color": "#ffffff", "bg_color": "#00000080", "size": "18"},
                broll_usage=False,
                caption_frequency=0.5,
                keep_duration_min=5.0,
                keep_duration_max=240.0,
                delete_pause=False,
                delete_mispronunciation=False,
                speed_optimization="relaxed",
                filter_appearance=True,
                color_tone="warm",
                caption_position="top",
                caption_style={"color": "#ffffff", "bg_color": "#00000080", "font_size": 18},
                supported_software=["premiere", "capcut", "vegas", "davinci"],
                priority=80
            ),
            StyleTemplate(
                id="emotion-story",
                name="情绪故事",
                description="情感类内容，强调镜头情绪和音乐氛围",
                theme="entertainment",
                rhythm="medium",
                camera_movement=["handheld", "slow_pan", "closeup"],
                transition_style="crossfade",
                caption_style={"color": "#ffffff", "bg_color": "transparent", "size": "16"},
                broll_usage=True,
                caption_frequency=0.3,
                keep_duration_min=4.0,
                keep_duration_max=300.0,
                delete_pause=False,
                delete_mispronunciation=False,
                speed_optimization="emotional",
                filter_appearance=True,
                color_tone="dramatic",
                caption_position="mixed",
                caption_style={"color": "#ffffff", "bg_color": "transparent", "font_size": 16},
                supported_software=["premiere", "capcut", "finalcut", "davinci"],
                priority=70
            ),
            StyleTemplate(
                id="comparison-review",
                name="对比测评",
                description="多产品对比测评，需要清晰的信息展示",
                theme="documentary",
                rhythm="medium",
                camera_movement=["static", "side_by_side"],
                transition_style="cut",
                caption_style={"color": "#000000", "bg_color": "#ffffff80", "size": "22"},
                broll_usage=True,
                caption_frequency=0.9,
                keep_duration_min=2.0,
                keep_duration_max=180.0,
                delete_pause=True,
                delete_mispronunciation=True,
                speed_optimization="compressed",
                filter_appearance=True,
                color_tone="neutral",
                caption_position="bottom",
                caption_style={"color": "#000000", "bg_color": "#ffffff80", "font_size": 22},
                supported_software=["premiere", "capcut", "finalcut", "vegas"],
                priority=75
            ),
            StyleTemplate(
                id="beat-collage",
                name="卡点集锦",
                description="节奏感强的混剪，适合娱乐类内容",
                theme="entertainment",
                rhythm="fast",
                camera_movement=["rapid", "zoom", "pan"],
                transition_style="hard_cut",
                caption_style={"color": "#ffffff", "bg_color": "#00000080", "size": "20"},
                broll_usage=True,
                caption_frequency=0.6,
                keep_duration_min=1.0,
                keep_duration_max=60.0,
                delete_pause=False,
                delete_mispronunciation=False,
                speed_optimization="compressed",
                filter_appearance=False,
                color_tone="vibrant",
                caption_position="bottom",
                caption_style={"color": "#ffffff", "bg_color": "#00000080", "font_size": 20},
                supported_software=["premiere", "capcut", "vegas", "finalcut"],
                priority=65
            )
        ]

    def _default_platforms(self) -> dict:
        """默认平台配置"""
        return {
            "tiktok": PlatformConfig(
                platform="tiktok",
                recommended_duration=[15, 30, 45, 60],
                title_style="clickbait",
                cover_style="dynamic",
                music_style="upbeat",
                cta_style="direct",
                other_rules={
                    "hashtag_count": 3,
                    "use_sound": True,
                    "auto_loop": True,
                    "for_you_page_optimized": True
                }
            ),
            "youtube": PlatformConfig(
                platform="youtube",
                recommended_duration=[60, 180, 360],
                title_style="descriptive",
                cover_style="engaging",
                music_style="contextual",
                cta_style="gentle",
                other_rules={
                    "description_optimization": True,
                    "playlist_integration": True,
                    "chapter_markers": True,
                    "end_cards": True
                }
            ),
            "shorts": PlatformConfig(
                platform="shorts",
                recommended_duration=[15, 30, 45],
                title_style="clickbait",
                cover_style="bold",
                music_style="high_energy",
                cta_style="direct",
                other_rules={
                    "sound_effects": True,
                    "auto_loop": True,
                    "hashtag_count": 2,
                    "vertical_only": True
                }
            ),
            "bilibili": PlatformConfig(
                platform="bilibili",
                recommended_duration=[120, 180, 360],
                title_style="informative",
                cover_style="artistic",
                music_style="background",
                cta_style="community_oriented",
                other_rules={
                    "danmaku_enabled": True,
                    "related_count": 4,
                    "comment_prompt": True,
                    "episode_numbering": True
                }
            ),
            "instagram": PlatformConfig(
                platform="instagram",
                recommended_duration=[30, 60, 90],
                title_style="social",
                cover_style="aesthetic",
                music_style="trending",
                cta_style="engagement_driven",
                other_rules={
                    "reels_format": True,
                    "carousel_idea": True,
                    "story_repost": True,
                    "hashtag_count": 5
                }
            )
        }

    def decide(
        self,
        raw_segments: list,
        transcript: Transcript,
        style_id: str = "koubo-zhishi",
        target_software: str = "premiere",
        custom_params: dict = None
    ) -> EditingDecision:
        """生成剪辑决策"""

        # 1. 获取风格模板
        style_template = next(
            (s for s in self.style_templates if s.id == style_id),
            self.style_templates[0]
        )

        # 2. 匹配最佳软件
        if target_software not in style_template.supported_software:
            target_software = style_template.supported_software[0]

        # 3. 生成动作列表
        actions = self._generate_actions(
            raw_segments,
            transcript,
            style_template
        )

        # 4. 生成风格配置
        style_config = self._generate_style_config(
            style_template,
            custom_params or {}
        )

        # 5. 生成统计信息
        statistics = self._generate_statistics(
            raw_segments,
            transcript,
            actions
        )

        # 6. 创建决策对象
        decision = EditingDecision(
            target_software=target_software,
            target_resolution="1080p",
            target_aspect_ratio="16:9",
            strategy=style_template.rhythm,
            actions=actions,
            style_config=style_config,
            style_recommendations=self._generate_style_recommendations(style_template),
            platform_recommendations=self._generate_platform_recommendations(style_template),
            statistics=statistics,
            input_source=transcript.segments[0].raw_id if transcript.segments else ""
        )

        return decision

    def _generate_actions(
        self,
        raw_segments: list,
        transcript: Transcript,
        style_template: StyleTemplate
    ) -> list[dict[str, Any]]:
        """根据风格模板生成具体剪辑动作"""

        actions = []

        # 分析转写片段
        speech_segments = transcript.segments

        for seg in speech_segments:
            action_type = self._determine_action_type(seg, style_template)

            if action_type == ActionType.DELETE_REGION:
                # 删除低质量/停顿/口误片段
                actions.append({
                    "type": "delete_region",
                    "start_sec": seg.start_sec,
                    "end_sec": seg.end_sec,
                    "reason": seg.is_mispronunciation and "口误删除" or "停顿删除",
                    "priority": "high"
                })

            elif action_type == ActionType.SPLIT:
                # 在适当时机切分
                actions.append({
                    "type": "split",
                    "position": seg.end_sec,
                    "context": "语义自然切点"
                })

            elif action_type == ActionType.SPEED_CHANGE:
                # 调整播放速度
                actions.append({
                    "type": "speed_change",
                    "start_sec": seg.start_sec,
                    "end_sec": seg.end_sec,
                    "new_speed": 1.1 if style_template.speed_optimization == "compressed" else 1.0,
                    "reason": "节奏优化"
                })

            # 可以扩展其他动作类型...

        return actions

    def _determine_action_type(self, seg: SpeechSegment, template: StyleTemplate) -> ActionType:
        """确定片段应执行的动作类型"""

        # 删除口误
        if template.delete_mispronunciation and seg.is_mispronunciation:
            return ActionType.DELETE_REGION

        # 删除过长停顿
        if template.delete_pause and seg.has_pause:
            return ActionType.DELETE_REGION

        # 自然切分点
        return ActionType.SPLIT

    def _generate_style_config(
        self,
        style_template: StyleTemplate,
        custom_params: dict
    ) -> dict[str, Any]:
        """生成风格配置"""

        config = {
            "template_id": style_template.id,
            "name": style_template.name,
            "theme": style_template.theme,
            "camera_movement": style_template.camera_movement,
            "transition_style": style_template.transition_style,
            "caption_style": style_template.caption_style,
            "broll_usage": style_template.broll_usage,
            "caption_frequency": style_template.caption_frequency,
            "keep_duration_range": [
                style_template.keep_duration_min,
                style_template.keep_duration_max
            ],
            "delete_pause": style_template.delete_pause,
            "delete_mispronunciation": style_template.delete_mispronunciation,
            "speed_optimization": style_template.speed_optimization,
            "filter_appearance": style_template.filter_appearance,
            "color_tone": style_template.color_tone,
            "caption_position": style_template.caption_position,
            "supported_software": style_template.supported_software
        }

        config.update(custom_params)
        return config

    def _generate_statistics(
        self,
        raw_segments: list,
        transcript: Transcript,
        actions: list
    ) -> dict[str, Any]:
        """生成统计信息"""

        total_segments = len(transcript.segments)
        total_duration = transcript.total_duration

        # 计算将要删除的时长
        deleted_duration = sum(
            a.get("end_sec", 0) - a.get("start_sec", 0)
            for a in actions if a.get("type") == "delete_region"
        )

        # 计算压缩比例
        compression_ratio = (deleted_duration / total_duration * 100) if total_duration > 0 else 0

        return {
            "total_segments": total_segments,
            "total_duration": round(total_duration, 2),
            "deleted_duration": round(deleted_duration, 2),
            "compression_ratio": round(compression_ratio, 2),
            "keep_segments": total_segments - sum(
                a["priority"] == "high"
                for a in actions if a["type"] == "delete_region"
            ),
            "action_count": len(actions)
        }

    def _generate_style_recommendations(self, template: StyleTemplate) -> list[str]:
        """生成风格建议"""

        recommendations = []

        if template.camera_movement:
            recommendations.append(f"建议使用 {','.join(template.camera_movement)} 镜头运动")

        if template.broll_usage:
            recommendations.append("适当添加B-roll增强视觉丰富度")

        if template.caption_frequency > 0.5:
            recommendations.append("添加更多字幕提高信息密度")

        if template.speed_optimization == "compressed":
            recommendations.append("可适当压缩节奏，控制在建议时长范围内")

        if template.filter_appearance:
            recommendations.append("考虑添加滤镜提升画面质感")

        return recommendations[:5]  # 返回前5条

    def _generate_platform_recommendations(self, template: StyleTemplate) -> list[str]:
        """生成平台推荐"""

        recs = []

        recs.append(f"适合 {template.theme} 类主题内容")

        recs.append(f"推荐时长范围: {template.keep_duration_min}-{template.keep_duration_max} 秒")

        recs.append(f"建议使用 {template.transition_style} 转场")

        return recs


def save_decision(decision: EditingDecision, output_path: Path) -> Path:
    """保存决策为JSON"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(decision.to_json(encoding="utf-8"), encoding="utf-8")
    return output_path


def load_decision(input_path: Path) -> EditingDecision:
    """加载决策"""
    json_str = input_path.read_text(encoding="utf-8")
    decision = EditingDecision(**json.loads(json_str))

    return decision
