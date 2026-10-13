"""
ai-editor-core - 智能剪辑工作流示例

演示如何使用ai_editor_core进行智能剪辑决策
"""

from pathlib import Path
from ai_editor_core import (
    run_ai_cut,
    parse_transcript,
    get_style_template,
    list_available_styles,
    EditingDecision,
    SpeechSegment
)
from ai_editor_core.core import StyleTemplate, PlatformConfig
from ai_editor_core.adapter import create_adapter


def example_basic_usage():
    """基础使用示例"""
    print("=== 基础使用示例 ===\n")

    # 1. 转写音频
    transcript = parse_transcript("path/to/audio.wav")
    print(f"转写完成，共 {len(transcript.segments)} 个片段")

    # 2. 生成决策
    decision = run_ai_cut(
        input_path="path/to/video.mp4",
        output_path="output/decision.json",
        style_id="koubo-zhishi",
        target_software="premiere"
    )

    print(f"决策生成完成")
    print(f"目标软件: {decision.target_software}")
    print(f"总时长: {decision.statistics['total_duration']} 秒")
    print(f"压缩比例: {decision.statistics['compression_ratio']}%")

    # 3. 导出到软件格式
    export_result = export_for_software(decision, "premiere", "output/premiere_script.jsx")
    print(f"\n已导出 {export_result['software']} 格式:\n")
    for instruction in export_result['instructions']:
        print(f"  - {instruction}")


def example_custom_style():
    """自定义风格模板示例"""
    print("\n=== 自定义风格示例 ===\n")

    # 创建自定义风格模板
    custom_template = StyleTemplate(
        id="custom-edgy",
        name="Edgy 动感风格",
        description="快速节奏，高对比度，适合科技类内容",
        theme="entertainment",
        rhythm="fast",
        camera_movement=["rapid", "dynamic", "zoom"],
        transition_style="hard_cut",
        caption_style={"color": "#00ff00", "bg_color": "#00000000", "size": "18"},
        broll_usage=True,
        caption_frequency=0.8,
        keep_duration_min=3.0,
        keep_duration_max=45.0,
        delete_pause=True,
        delete_mispronunciation=True,
        speed_optimization="compressed",
        filter_appearance=True,
        color_tone="high_contrast",
        caption_position="top",
        caption_style={"color": "#ffffff", "bg_color": "#00000080", "font_size": 20},
        supported_software=["premiere", "capcut", "vegas", "finalcut"],
        priority=95
    )

    print(f"自定义风格: {custom_template.name}")
    print(f"描述: {custom_template.description}")
    print(f"主题: {custom_template.theme}")
    print(f"节奏: {custom_template.rhythm}")
    print(f"推荐时长: {custom_template.keep_duration_min}-{custom_template.keep_duration_max}秒")


def example_platform_specific():
    """平台特定示例"""
    print("\n=== 平台特定示例 ===\n")

    # 获取平台配置
    tiktok_config = get_platform_config("tiktok")
    print(f"TikTok 平台推荐时长: {tiktok_config.recommended_duration}")
    print(f"标题风格: {tiktok_config.title_style}")
    print(f"音乐风格: {tiktok_config.music_style}")
    print(f"CTA风格: {tiktok_config.cta_style}")

    youtube_config = get_platform_config("youtube")
    print(f"\nYouTube 平台推荐时长: {youtube_config.recommended_duration}")
    print(f"标题风格: {youtube_config.title_style}")
    print(f"描述优化: {youtube_config.other_rules['description_optimization']}")


def example_style_comparison():
    """风格对比示例"""
    print("\n=== 风格对比 ===\n")

    # 列出所有风格
    styles = list_available_styles()

    print(f"可用风格数: {len(styles)}")
    print("\n主要风格对比:\n")

    categories = {
        "education": [],
        "entertainment": [],
        "documentary": []
    }

    for style in styles:
        if style['theme'] in categories:
            categories[style['theme']].append(style)

    for theme, style_list in categories.items():
        print(f"\n{style_list[0]['theme'].upper()} 风格:")
        for style in style_list:
            print(f"  - {style['name']}: {style['rhythm']}节奏, 优先级{style['priority']}")


def example_export_to_different_software():
    """导出到不同软件示例"""
    print("\n=== 软件导出示例 ===\n")

    # 创建一个测试决策
    decision = EditingDecision(
        target_software="premiere",
        target_resolution="1080p",
        target_aspect_ratio="16:9",
        strategy="compressed",
        style_config={
            "name": "教育风格",
            "theme": "education"
        },
        actions=[
            {"type": "delete_region", "start_sec": 0.0, "end_sec": 2.0, "reason": "口误"},
            {"type": "split", "position": 15.0, "context": "语义切点"},
            {"type": "delete_region", "start_sec": 30.0, "end_sec": 35.0, "reason": "停顿"}
        ]
    )

    # 导出到Premiere
    print("导出到 Premiere Pro:")
    result = export_for_software(decision, "premiere", "output/premiere_output.json")
    print(f"  {result['software']} - {result['output_format']}格式")
    print(f"  操作数: {len(decision.actions)}")

    # 导出到CapCut
    print("\n导出到 CapCut:")
    result = export_for_software(decision, "capcut", "output/capcut_output.json")
    print(f"  {result['software']} - {result['output_format']}格式")

    # 导出到Final Cut
    print("\n导出到 Final Cut Pro:")
    result = export_for_software(decision, "finalcut", "output/fcpx_output.json")
    print(f"  {result['software']} - {result['output_format']}格式")


def example_statistics_analysis():
    """统计分析示例"""
    print("\n=== 统计分析示例 ===\n")

    decision = run_ai_cut(
        input_path="path/to/video.mp4",
        output_path="output/decision.json",
        style_id="koubo-zhishi"
    )

    stats = decision.statistics

    print("剪辑统计:")
    print(f"  总片段数: {stats['total_segments']}")
    print(f"  总时长: {stats['total_duration']} 秒")
    print(f"  删除时长: {stats['deleted_duration']} 秒")
    print(f"  压缩率: {stats['compression_ratio']}%")
    print(f"  保留片段: {stats['keep_segments']}")
    print(f"  动作数: {stats['action_count']}")

    print(f"\n风格建议:")
    for rec in decision.style_recommendations:
        print(f"  • {rec}")

    print(f"\n平台推荐:")
    for rec in decision.platform_recommendations:
        print(f"  • {rec}")


def example_realtime_analysis():
    """实时分析示例"""
    print("\n=== 实时分析示例 ===\n")

    print("实时分析功能演示:")
    print("1. 创建实时视觉分析器")
    print("2. 设置摄像头或视频文件")
    print("3. 启动实时分析")

    print("\n功能特性:")
    print("  • 实时帧级画面分析")
    print("  • 质量检测（清晰度、亮度、噪声）")
    print("  • 运动检测（运动程度、场景类型）")
    print("  • 人物检测（面部识别、人数统计）")
    print("  • 场景识别（口播/环境/移动）")
    print("  • 实时质量评分")
    print("  • 动态建议生成")

    print("\n使用示例:")
    print("""
    from ai_editor_core import RealtimeVisualAnalyzer

    # 创建分析器
    analyzer = RealtimeVisualAnalyzer(target_width=1920, target_height=1080)

    # 设置摄像头
    analyzer.set_video_source(0)  # 摄像头编号

    # 启动分析
    if analyzer.start():
        while True:
            analysis = analyzer.update()

            if analysis:
                print(f"帧 {analysis.frame_number} | 质量: {analysis.avg_quality:.2f}")
                print(f"  清晰度: {analysis.sharpness_score:.2f}")
                print(f"  亮度: {analysis.brightness_score:.2f}")
                print(f"  运动度: {analysis.motion_level:.2f}")
                print(f"  面部: {'检测到' if analysis.face_detected else '未检测'}")
                print(f"  场景类型: {analysis.scene_type}")

                # 获取建议
                for suggestion in analysis.suggestions:
                    print(f"  建议: {suggestion}")
    """)


if __name__ == "__main__":
    print("=" * 60)
    print("AI Editor Core - 智能剪辑工作流示例")
    print("=" * 60)

    # 运行各种示例
    example_basic_usage()
    example_custom_style()
    example_platform_specific()
    example_style_comparison()
    example_export_to_different_software()
    example_statistics_analysis()

    print("\n" + "=" * 60)
    print("示例运行完成!")
    print("=" * 60)


def example_realtime_decisions():
    """实时决策演示"""
    print("\n=== 实时决策演示 ===\n")

    print("实时决策系统功能:")
    print("1. 结合视觉分析和AI决策生成实时建议")
    print("2. 实时标记可删除片段")
    print("3. 动态调整剪辑策略")
    print("4. 实时质量监控")

    print("\n使用示例:")
    print("""
    from ai_editor_core import RealtimeDecisionEngine

    # 创建实时决策引擎
    engine = RealtimeDecisionEngine(video_source=0)  # 摄像头

    # 设置剪辑策略
    engine.set_strategy(style_id="koubo-zhishi", target_software="premiere")

    # 添加建议回调
    def on_suggestion(suggestion):
        print(f"[{suggestion.timestamp:.2f}秒] {suggestion.reason} ({suggestion.priority})")

    engine.add_suggestion_callback(on_suggestion)

    # 添加质量警报回调
    def on_quality_alert(quality):
        if quality < engine.min_quality_threshold:
            print(f"警报: 画面质量较低 ({quality:.2f})")

    engine.add_quality_alert_callback(on_quality_alert)

    # 启动
    if engine.start():
        while True:
            decision = engine.update()

            if decision:
                # 获取最新建议
                suggestions = engine.get_active_suggestions()

                # 获取决策历史
                history = engine.get_decision_history(limit=10)

                # 生成最终决策
                final_decision = engine.generate_final_decision()
    """)
