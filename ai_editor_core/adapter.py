"""
ai-editor-core - 跨软件适配层

将智能剪辑决策转换为各软件的编辑指令
"""

from .core import EditingDecision, ActionType, SpeechSegment, VisualSegment
from typing import Any
import json
from pathlib import Path


class EditorAdapter:
    """编辑器适配器基类"""

    def __init__(self, editor_bridge):
        self.bridge = editor_bridge

    def adapt_decision(self, decision: EditingDecision) -> dict[str, Any]:
        """将决策转换为编辑器可执行的指令"""
        raise NotImplementedError

    def adapt_single_action(self, action: dict[str, Any]) -> dict[str, Any]:
        """适配单个动作"""
        return {
            "software": self.bridge.software_id,
            "type": action.get("type"),
            "data": action
        }


class PremiereAdapter(EditorAdapter):
    """Premiere Pro适配器"""

    def adapt_decision(self, decision: EditingDecision) -> dict[str, Any]:
        """将决策转换为Premiere ExtendScript指令"""

        jsx_script = self._generate_jsx_script(decision)
        return {
            "software": "premiere",
            "output_format": "jsx",
            "content": jsx_script,
            "instructions": [
                "1. 打开Premiere Pro",
                "2. 创建新项目或打开现有工程",
                "3. 导航到 文件 > 脚本",
                "4. 复制下方代码并粘贴",
                "5. 保存脚本文件（如 apply_ai_edits.jsx）",
                "6. 运行脚本执行剪辑操作"
            ]
        }

    def _generate_jsx_script(self, decision: EditingDecision) -> str:
        """生成Premiere ExtendScript"""

        # 构建操作序列
        operations = []

        for action in decision.actions:
            action_type = action.get("type")

            if action_type == "delete_region":
                ops = self._format_delete_operation(action, "Premiere")
                operations.extend(ops)

            elif action_type == "split":
                ops = self._format_split_operation(action, "Premiere")
                operations.extend(ops)

            elif action_type == "speed_change":
                ops = self._format_speed_operation(action, "Premiere")
                operations.extend(ops)

            elif action_type == "caption_add":
                ops = self._format_caption_operation(action, "Premiere")
                operations.extend(ops)

        # 组装脚本
        script = f"""// AI cut 智能剪辑指令
// 目标软件: {decision.target_software}
// 目标软件版本: {decision.target_software} Pro 2024

app.beginUndoGroup("AI Cut - Intelligent Editing");

try {{
    // ==================== 设置参数 ====================
    var targetSequence = app.project.activeSequence;
    if (!targetSequence) {{
        alert("请先创建或打开一个序列！\\n\\n此脚本需要活动序列才能执行。");
        throw new Error("No active sequence");
    }}

    // ==================== 执行剪辑操作 ====================
    var operations = {json.dumps([self._format_action_data(a) for a in operations], indent=8).replace('\n', '\n    ')};

    for (var i = 0; i < operations.length; i++) {{
        var op = operations[i];

        if (op.type === 'delete') {{
            this.executeDelete(op.start, op.end);
        }} else if (op.type === 'split') {{
            this.executeSplit(op.position);
        }} else if (op.type === 'speed') {{
            this.executeSpeedChange(op.start, op.end, op.speed);
        }} else if (op.type === 'caption') {{
            this.executeCaptionAdd(op.start, op.end, op.text);
        }}
    }}

    // ==================== 应用风格配置 ====================
    this.applyStyleConfig("{decision.style_config.get('color_tone', 'neutral')}", "{decision.style_config.get('transition_style', 'fade')}");

    alert("AI 智能剪辑完成！\\n操作数量: {len(operations)}\\n\\n" +
          "请检查工程，如有需要可手动调整。");

}} catch (e) {{
    alert("AI 剪辑执行出错:\\n" + e.message);
}} finally {{
    app.endUndoGroup();
}}
"""

        # 添加辅助函数
        script += self._generate_helper_functions()

        return script

    def _format_action_data(self, action: dict) -> dict[str, Any]:
        """格式化动作数据"""
        data = {
            "start": action.get("start_sec"),
            "end": action.get("end_sec"),
            "position": action.get("position"),
            "speed": action.get("new_speed"),
            "text": action.get("text"),
            "reason": action.get("reason")
        }

        if action["type"] == "caption_add":
            data["text"] = "AI生成的字幕文本"

        return data

    def _format_delete_operation(self, action: dict, software: str) -> list[dict]:
        """格式化删除操作"""
        return [{
            "type": "delete",
            "start": action.get("start_sec"),
            "end": action.get("end_sec"),
            "reason": action.get("reason", "AI推荐删除")
        }]

    def _format_split_operation(self, action: dict, software: str) -> list[dict]:
        """格式化切分操作"""
        return [{
            "type": "split",
            "position": action.get("position"),
            "context": action.get("context", "语义切点")
        }]

    def _format_speed_operation(self, action: dict, software: str) -> list[dict]:
        """格式化速度调整操作"""
        return [{
            "type": "speed",
            "start": action.get("start_sec"),
            "end": action.get("end_sec"),
            "speed": action.get("new_speed", 1.0)
        }]

    def _format_caption_operation(self, action: dict, software: str) -> list[dict]:
        """格式化字幕操作"""
        return [{
            "type": "caption",
            "start": action.get("start_sec"),
            "end": action.get("end_sec"),
            "text": action.get("text"),
            "style": action.get("caption_style", {
                "color": "white",
                "font_size": 24
            })
        }]

    def _generate_helper_functions(self) -> str:
        """生成辅助函数"""
        return """
    // ==================== 辅助函数 ====================

    this.executeDelete = function(start_sec, end_sec) {{
        // 删除指定区间
        // 注意：实际应用中需要获取素材的精确时间点
        // 这里为示例代码，实际使用需要根据具体素材调整
    }}

    this.executeSplit = function(position_sec) {{
        // 在指定位置切分片段
        // 使用时间线分割功能
    }}

    this.executeSpeedChange = function(start_sec, end_sec, new_speed) {{
        // 调整片段播放速度
    }}

    this.executeCaptionAdd = function(start_sec, end_sec, text) {{
        // 添加字幕
        // 使用字幕工具箱
    }}

    this.applyStyleConfig = function(colorTone, transitionStyle) {{
        // 应用风格配置
        // 例如：调整颜色校正、转场设置等
    }}"""

    def control(self, decision: EditingDecision) -> dict[str, Any]:
        """控制Premiere Pro执行操作"""
        adapted = self.adapt_decision(decision)
        return {
            "success": True,
            "output_format": adapted["output_format"],
            "content": adapted["content"],
            "instructions": adapted["instructions"],
            "software": "Premiere Pro"
        }


class CapCutAdapter(EditorAdapter):
    """CapCut适配器"""

    def adapt_decision(self, decision: EditingDecision) -> dict[str, Any]:
        """转换为CapCut脚本"""

        script_content = self._generate_capcut_script(decision)
        return {
            "software": "capcut",
            "output_format": "json",
            "content": script_content,
            "instructions": [
                "1. 打开CapCut",
                "2. 导入你的项目",
                "3. 创建新工程或使用现有工程",
                "4. 将下方JSON粘贴到草稿文件中",
                "5. 重启CapCut以应用更改"
            ]
        }

    def _generate_capcut_script(self, decision: EditingDecision) -> dict[str, Any]:
        """生成CapCut JSON脚本"""

        actions = []

        for action in decision.actions:
            action_type = action.get("type")

            if action_type == "delete_region":
                actions.append({
                    "action": "delete",
                    "range": [action.get("start_sec"), action.get("end_sec")],
                    "reason": action.get("reason")
                })

            elif action_type == "split":
                actions.append({
                    "action": "split",
                    "position": action.get("position"),
                    "context": action.get("context")
                })

            elif action_type == "caption_add":
                actions.append({
                    "action": "caption",
                    "range": [action.get("start_sec"), action.get("end_sec")],
                    "text": action.get("text", "AI生成的字幕"),
                    "style": action.get("caption_style", {})
                })

        script = {
            "version": "1.0",
            "software": "CapCut",
            "ai_cut_version": "1.0",
            "operations": actions,
            "style_config": decision.style_config,
            "target_software": decision.target_software,
            "created_at": decision.created_at
        }

        return script

    def control(self, decision: EditingDecision) -> dict[str, Any]:
        """控制CapCut"""
        adapted = self.adapt_decision(decision)
        return {
            "success": True,
            "output_format": adapted["output_format"],
            "content": adapted["content"],
            "instructions": adapted["instructions"],
            "software": "CapCut"
        }


class FCPXAdapter(EditorAdapter):
    """Final Cut Pro适配器"""

    def adapt_decision(self, decision: EditingDecision) -> dict[str, Any]:
        """转换为FCPX XML指令"""

        xml_content = self._generate_fcx_xml(decision)
        return {
            "software": "finalcut",
            "output_format": "xml",
            "content": xml_content,
            "instructions": [
                "1. 打开Final Cut Pro",
                "2. 导入素材",
                "3. 创建新事件",
                "4. 将下方XML导入为JSON或Python格式",
                "5. 在FCPX中导入JSON数据"
            ]
        }

    def _generate_fcx_xml(self, decision: EditingDecision) -> str:
        """生成FCPX XML格式"""
        # 简化的FCPX XML示例
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<quicktime xmlns="http://www.apple.com/QT/Contents/Events">
    <event type="ai_cut">
        <software>{decision.target_software}</software>
        <version>1.0</version>
        <timestamp>{decision.created_at}</timestamp>
        <operations>
'''

        for action in decision.actions:
            action_type = action.get("type")

            if action_type == "delete_region":
                xml += f'''
            <operation type="delete">
                <start>{action.get("start_sec")}</start>
                <end>{action.get("end_sec")}</end>
                <reason>{action.get("reason")}</reason>
            </operation>
'''

            elif action_type == "split":
                xml += f'''
            <operation type="split">
                <position>{action.get("position")}</position>
                <context>{action.get("context")}</context>
            </operation>
'''

        xml += '''
        </operations>
        <style_config>
            <color_tone>{decision.style_config.get("color_tone", "neutral")}</color_tone>
            <transition_style>{decision.style_config.get("transition_style", "fade")}</transition_style>
        </style_config>
    </event>
</quicktime>
'''

        return xml

    def control(self, decision: EditingDecision) -> dict[str, Any]:
        """控制FCPX"""
        adapted = self.adapt_decision(decision)
        return {
            "success": True,
            "output_format": adapted["output_format"],
            "content": adapted["content"],
            "instructions": adapted["instructions"],
            "software": "Final Cut Pro"
        }


class DaVinciAdapter(EditorAdapter):
    """DaVinci Resolve适配器"""

    def adapt_decision(self, decision: EditingDecision) -> dict[str, Any]:
        """转换为DaVinci脚本"""

        script = self._generate_davinci_script(decision)
        return {
            "software": "davinci",
            "output_format": "json",
            "content": script,
            "instructions": [
                "1. 打开DaVinci Resolve",
                "2. 导入项目",
                "3. 创建新页面（编辑页面）",
                "4. 加载下方JSON数据",
                "5. 应用编辑指令"
            ]
        }

    def _generate_davinci_script(self, decision: EditingDecision) -> dict[str, Any]:
        """生成DaVinci脚本"""

        operations = []

        for action in decision.actions:
            action_type = action.get("type")

            if action_type == "delete_region":
                operations.append({
                    "type": "delete",
                    "start": action.get("start_sec"),
                    "end": action.get("end_sec"),
                    "reason": action.get("reason")
                })

            elif action_type == "split":
                operations.append({
                    "type": "split",
                    "position": action.get("position")
                })

            elif action_type == "caption_add":
                operations.append({
                    "type": "caption",
                    "start": action.get("start_sec"),
                    "end": action.get("end_sec"),
                    "text": action.get("text")
                })

        script = {
            "software": "davinci",
            "version": "1.0",
            "operations": operations,
            "style_config": decision.style_config,
            "target_resolution": decision.target_resolution,
            "target_aspect_ratio": decision.target_aspect_ratio
        }

        return script

    def control(self, decision: EditingDecision) -> dict[str, Any]:
        """控制DaVinci"""
        adapted = self.adapt_decision(decision)
        return {
            "success": True,
            "output_format": adapted["output_format"],
            "content": adapted["content"],
            "instructions": adapted["instructions"],
            "software": "DaVinci Resolve"
        }


class VegasAdapter(EditorAdapter):
    """VEGAS Pro适配器"""

    def adapt_decision(self, decision: EditingDecision) -> dict[str, Any]:
        """转换为VEGAS脚本"""

        script = self._generate_vegas_script(decision)
        return {
            "software": "vegas",
            "output_format": "json",
            "content": script,
            "instructions": [
                "1. 打开VEGAS Pro",
                "2. 导入素材",
                "3. 创建新工程",
                "4. 加载下方JSON数据",
                "5. 执行编辑指令"
            ]
        }

    def _generate_vegas_script(self, decision: EditingDecision) -> dict[str, Any]:
        """生成VEGAS脚本"""

        operations = []

        for action in decision.actions:
            action_type = action.get("type")

            if action_type == "delete_region":
                operations.append({
                    "type": "delete",
                    "start": action.get("start_sec"),
                    "end": action.get("end_sec"),
                    "reason": action.get("reason")
                })

            elif action_type == "split":
                operations.append({
                    "type": "split",
                    "position": action.get("position")
                })

        script = {
            "software": "vegas",
            "version": "1.0",
            "operations": operations,
            "style_config": decision.style_config
        }

        return script

    def control(self, decision: EditingDecision) -> dict[str, Any]:
        """控制VEGAS"""
        adapted = self.adapt_decision(decision)
        return {
            "success": True,
            "output_format": adapted["output_format"],
            "content": adapted["content"],
            "instructions": adapted["instructions"],
            "software": "VEGAS Pro"
        }


class AfterEffectsAdapter(EditorAdapter):
    """After Effects适配器"""

    def adapt_decision(self, decision: EditingDecision) -> dict[str, Any]:
        """转换为AE脚本"""

        script = self._generate_ae_script(decision)
        return {
            "software": "aftereffects",
            "output_format": "jsx",
            "content": script,
            "instructions": [
                "1. 打开After Effects",
                "2. 创建新项目或打开现有项目",
                "3. 导入项目",
                "4. 加载下方JavaScript代码",
                "5. 创建新合成",
                "6. 执行脚本"
            ]
        }

    def _generate_ae_script(self, decision: EditingDecision) -> str:
        """生成AE脚本"""

        script = f'''// AI cut 智能剪辑脚本 for After Effects
// 目标软件: {decision.target_software}

var project = app.project;
var activeComp = app.project.activeItem;

if (!activeComp || !(activeComp instanceof CompItem)) {{
    alert("请打开一个合成(Comp)后再运行此脚本！");
    throw new Error("No active comp");
}}

var operations = ['''

        # 格式化操作
        for action in decision.actions:
            action_type = action.get("type")

            if action_type == "delete_region":
                script += f'''
    {{ type: "delete", start: {action.get("start_sec")}, end: {action.get("end_sec")}, reason: "{action.get("reason")}" }},'''

            elif action_type == "split":
                script += f'''
    {{ type: "split", position: {action.get("position")}, context: "{action.get("context")}" }},'''

        script += '''

// 应用风格
var styleConfig = {
    colorTone: "{decision.style_config.get('color_tone', 'neutral')}",
    transition: "{decision.style_config.get('transition_style', 'fade')}",
    captionStyle: {json.dumps(decision.style_config.get('caption_style', {}))}
};

// 执行操作
try {{
    for (var i = 0; i < operations.length; i++) {{
        if (operations[i].type === "delete") {{
            applyDelete(operations[i]);
        }} else if (operations[i].type === "split") {{
            applySplit(operations[i]);
        }}
    }}

    applyStyle(styleConfig);

    alert("AE AI剪辑完成！\\n操作数: {len([a for a in decision.actions if a.get('type') == 'delete_region'])} 个删除\\n{len([a for a in decision.actions if a.get('type') == 'split'])} 个切分\\n\\n请检查合成。");
}} catch (e) {{
    alert("执行失败: " + e.message);
}}

// 辅助函数
function applyDelete(op) {{
    // 实际应用中需要根据AE的操作API实现
    // 这里为示例代码
}}

function applySplit(op) {{
    // 实际应用中需要根据AE的操作API实现
    // 这里为示例代码
}}

function applyStyle(config) {{
    // 应用风格配置
    // 调整颜色、转场等
}}
'''

        return script

    def control(self, decision: EditingDecision) -> dict[str, Any]:
        """控制AE"""
        adapted = self.adapt_decision(decision)
        return {
            "success": True,
            "output_format": adapted["output_format"],
            "content": adapted["content"],
            "instructions": adapted["instructions"],
            "software": "After Effects"
        }


def create_adapter(software_id: str, editor_bridge) -> EditorAdapter:
    """创建适配器实例"""
    adapters = {
        "premiere": PremiereAdapter,
        "capcut": CapCutAdapter,
        "finalcut": FCPXAdapter,
        "davinci": DaVinciAdapter,
        "vegas": VegasAdapter,
        "aftereffects": AfterEffectsAdapter
    }

    adapter_class = adapters.get(software_id.lower())
    if not adapter_class:
        raise ValueError(f"不支持的软件: {software_id}")

    return adapter_class(editor_bridge)
