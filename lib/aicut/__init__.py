r"""aicut · AI 剪辑主体的共享内核。

分层：
  paths    工程路径与配置
  draft    剪映草稿 -> 统一 IR（读）
  edit     草稿编辑原语（写，工程通道）
  diff     IR 差异（验证用的结构化信号）
  memory   memory.sqlite 记忆库（任务/技能/偏好/快照/视觉事件）
  snapshot 草稿快照与回滚
  verify   断言引擎（结构 / 视觉 / 语义）
  vision   素材视觉事件流
  llm      模型客户端（云端 + 本地兜底）
  plan     目标 -> EDL 意图
  edl      意图 -> 工程操作
  agent    主体循环
"""

__version__ = "5.3.0"

import importlib as _importlib

# Source releases omit model runtimes and the historical editor component.
# Keep standard-library tooling usable while reporting which skill groups need
# external dependencies. Actual feature execution must still satisfy its gates.
OPTIONAL_IMPORT_ERRORS = {}
for _module in (
    'skills', 'skills_material', 'skills_maker', 'skills_ui', 'skills_recipe',
    'skills_edit', 'skills_text', 'skills_realtime', 'skills_review',
    'skills_style', 'skills_retake', 'capcut_auto',
):
    try:
        _importlib.import_module(f'.{_module}', __name__)
    except ModuleNotFoundError as _exc:
        if (_exc.name or '').split('.')[0] in {'aicut', 'editor_bridges', 'smart_export'}:
            raise
        OPTIONAL_IMPORT_ERRORS[_module] = _exc.name

__all__ = ["__version__", "OPTIONAL_IMPORT_ERRORS"]
