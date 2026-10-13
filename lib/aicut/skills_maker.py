"""The planner's production tools call MakerAPI instead of writing ad-hoc scripts."""
from . import skills
from .maker_api import MakerAPI


def run_tool(ctx, name, args):
    try:
        api = MakerAPI()
        report = api.call_tool(name, args, approvals=ctx.production_approvals)
        return {**report, 'op': name, 'stop_agent': True,
                'note': ('制作接口未执行成功，详见阻断原因' if not report['ok'] else
                         '方案已检查，等待确认后调用制作接口' if name == 'inspect_video_plan'
                         else '制作接口已返回；具体产物和验收状态见交付报告')}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'op': name, 'ok': False, 'status': 'blocked', 'stop_agent': True,
                'error': str(exc)}


skills.register(skills.Skill(
    id='inspect_video_plan', goal='检查大脑生成的决策单，选择目标软件与制作方式，返回待确认请求',
    channel='production', args={'sheet': '决策单JSON路径', 'software': '目标软件ID',
                                'engine': 'project草稿或portable独立MP4',
                                'output': '可选MP4输出路径', 'publish': '是否复制到剪映草稿库，默认false'},
    required=['sheet', 'software', 'engine'], sample={},
    fn=lambda ctx, args: run_tool(ctx, 'inspect_video_plan', args),
    notes='只检查与准备；不得把待确认请求称为已经生成视频',
))
skills.register(skills.Skill(
    id='make_video', goal='用户确认后调用制作接口生成草稿或独立MP4，并读取实际交付结果',
    channel='production', args={'request_id': '已准备且由应用记录用户确认的请求ID'},
    required=['request_id'], sample={},
    fn=lambda ctx, args: run_tool(ctx, 'make_video', args),
    notes='确认数据由应用上下文提供，模型不能自我批准；原生自动导出未验收会被阻止',
))
