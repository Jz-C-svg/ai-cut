"""
ai-editor-core - 主模块入口

提供简洁的API用于智能剪辑决策
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
from .engine import DecisionEngine
from .adapter import create_adapter
from .realtime_visual_analyzer import (
    FrameAnalysis,
    RealtimeVisualAnalyzer,
    AnalysisResult
)
from .realtime_decision_engine import (
    RealtimeSuggestion,
    RealtimeDecision,
    RealtimeDecisionEngine
)

__version__ = "1.0.0"


def run_ai_cut(
    input_path: str | Path,
    output_path: str | Path,
    style_id: str = "koubo-zhishi",
    target_software: str = "premiere",
    custom_params: dict = None
) -> EditingDecision:
    """
    完整的AI剪辑工作流

    参数:
        input_path: 输入素材路径（视频或音频）
        output_path: 输出决策文件路径（JSON）
        style_id: 风格模板ID
        target_software: 目标软件ID
        custom_params: 自定义参数

    返回:
        EditingDecision: 生成的剪辑决策
    """
    from pathlib import Path
    import json

    # 1. 初始化引擎
    engine = DecisionEngine()

    # 2. 解析输入（简化版，实际需要对接你的ASR和视觉分析）
    transcript = _parse_input(input_path)

    # 3. 生成决策
    decision = engine.decide(
        raw_segments=[],
        transcript=transcript,
        style_id=style_id,
        target_software=target_software,
        custom_params=custom_params or {}
    )

    # 4. 保存决策
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(decision.to_json())

    return decision


def parse_transcript(audio_path: str | Path) -> Transcript:
    """
    解析音频生成转写结果

    注意: 这里是简化实现，实际应该使用 faster-whisper 等 ASR 引擎
    """
    from pathlib import Path

    # 模拟解析
    return Transcript(
        segments=[
            SpeechSegment(
                raw_id="seg_001",
                start_sec=0.0,
                length=5.0,
                text="这是一段示例文本，实际使用时会替换为语音转写结果",
                speaker="speaker",
                language="zh",
                confidence=0.95
            )
        ],
        total_duration=5.0,
        languages=["zh"],
        confidence_score=0.95
    )


def get_style_template(style_id: str) -> StyleTemplate:
    """
    获取风格模板

    参数:
        style_id: 风格模板ID

    返回:
        StyleTemplate: 风格模板对象
    """
    engine = DecisionEngine()
    for template in engine.style_templates:
        if template.id == style_id:
            return template
    raise ValueError(f"未找到风格模板: {style_id}")


def get_platform_config(platform_id: str) -> PlatformConfig:
    """
    获取平台配置

    参数:
        platform_id: 平台ID

    返回:
        PlatformConfig: 平台配置对象
    """
    engine = DecisionEngine()
    if platform_id in engine.platform_configs:
        return engine.platform_configs[platform_id]
    raise ValueError(f"未找到平台配置: {platform_id}")


def export_for_software(
    decision: EditingDecision,
    software_id: str,
    output_path: str | Path
) -> dict[str, Any]:
    """
    将决策导出为指定软件的格式

    参数:
        decision: 剪辑决策
        software_id: 软件 ID
        output_path: 输出路径

    返回:
        dict: 导出结果
    """
    from pathlib import Path
    from .adapter import create_adapter

    adapter = create_adapter(software_id, None)
    result = adapter.control(decision)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if result["output_format"] == "json":
        output_path.write_text(json.dumps(result["content"], ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output_path.write_text(result["content"], encoding="utf-8")

    return result


def list_available_styles() -> list[dict[str, Any]]:
    """列出所有可用的风格模板"""
    engine = DecisionEngine()
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "theme": t.theme,
            "rhythm": t.rhythm,
            "priority": t.priority
        }
        for t in engine.style_templates
    ]


def list_available_platforms() -> list[dict[str, Any]]:
    """列出所有可用的平台配置"""
    engine = DecisionEngine()
    return [
        {
            "id": p.platform,
            "name": p.platform,
            "recommended_duration": p.recommended_duration,
            "title_style": p.title_style
        }
        for p in engine.platform_configs.values()
    ]


# 辅助函数
def _parse_input(input_path: Path) -> Transcript:
    """
    解析输入文件生成转写结果

    实际实现需要对接你的ASR引擎
    """
    import json

    # 如果是JSON文件，直接加载
    if input_path.suffix == ".json":
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return Transcript(
            segments=[
                SpeechSegment(
                    raw_id=f"seg_{i}",
                    start_sec=s.get("start_sec", 0.0),
                    length=s.get("length", 0.0),
                    text=s.get("text", ""),
                    speaker=s.get("speaker", "speaker"),
                    language=s.get("language", "zh"),
                    confidence=s.get("confidence", 0.9),
                    tags=s.get("tags", []),
                    is_mispronunciation=s.get("is_mispronunciation", False),
                    has_pause=s.get("has_pause", False)
                )
                for i, s in enumerate(data.get("segments", []))
            ],
            total_duration=data.get("total_duration", 0.0),
            languages=data.get("languages", []),
            confidence_score=data.get("confidence_score", 0.0)
        )

    # 否则创建模拟数据
    return parse_transcript(input_path)


# 导出所有公共API
__all__ = [
    "run_ai_cut",
    "parse_transcript",
    "get_style_template",
    "get_platform_config",
    "export_for_software",
    "list_available_styles",
    "list_available_platforms",
    "EditingDecision",
    "SpeechSegment",
    "VisualSegment",
    "Transcript",
    "ActionType",
    "StyleTemplate",
    "PlatformConfig",
    # 实时功能
    "FrameAnalysis",
    "RealtimeVisualAnalyzer",
    "AnalysisResult",
    "RealtimeSuggestion",
    "RealtimeDecision",
    "RealtimeDecisionEngine"
]
