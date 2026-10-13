r"""界面通道技能：只做工程文件做不到的事（花字、云字体、特效库、模板、导出）。

安全约定（和 ui.py 一致）：
  · 键位白名单，禁止 Ctrl+Q 之类
  · 每个动作都校验前台进程
  · --dry-run 时只说明打算按什么键，绝不真的动手
  · 剪映没开就明确报错，不假装成功
"""
from __future__ import annotations

from . import skills, ui


def _dry(ctx: skills.Context, what: str) -> dict:
    return {"op": what, "ok": True, "note": f"演算：界面通道不会真的动手（{what}）"}


def skill_ui_status(ctx: skills.Context, args: dict) -> dict:
    info = ui.info()
    page = info.get("page", "未知")
    return {
        "op": "ui_status",
        "ok": bool(info.get("running")),
        "running": info.get("running"),
        "pid": info.get("pid"),
        "page": page,
        "window": f"{info.get('width')}x{info.get('height')}",
        "note": (f"剪映在{page}（pid {info.get('pid')}，窗口 {info.get('width')}x{info.get('height')}）"
                 if info.get("running") else "剪映没开"),
    }


def skill_ui_press(ctx: skills.Context, args: dict) -> dict:
    keys = str(args["keys"])
    if ctx.dry_run:
        return _dry(ctx, f"按键 {keys}")
    res = ui.key(keys)
    return {"op": "ui_press", "ok": True, **res, "note": f"已发按键 {keys}（{res['cost_ms']}ms）"}


def skill_ui_open_draft(ctx: skills.Context, args: dict) -> dict:
    name = str(args["name"])
    if ctx.dry_run:
        return _dry(ctx, f"打开草稿 {name}")
    res = ui.open_draft(name)
    return {"op": "ui_open_draft", "ok": bool(res["opened"]), "name": name, **res,
            "note": f"{name} -> {res['page']}"}


def skill_ui_export(ctx: skills.Context, args: dict) -> dict:
    if ctx.dry_run:
        return _dry(ctx, "触发导出")
    res = ui.export()
    return {"op": "ui_export", "ok": bool(res["changed"]), **res,
            "note": f"导出面板{'已弹出' if res['changed'] else '没变化（可能没在编辑页）'}"}


def skill_ui_refresh(ctx: skills.Context, args: dict) -> dict:
    """重启剪映，让刚生成的草稿出现在列表里（重操作，必须显式确认）。"""
    if str(args.get("confirm", "")).lower() not in ("yes", "true", "确认"):
        return {"op": "ui_refresh", "ok": False,
                "error": "这是重操作（会关闭并重开剪映），需要 confirm=yes 才执行"}
    if ctx.dry_run:
        return _dry(ctx, "重启剪映")
    res = ui.refresh_jianying()
    return {"op": "ui_refresh", "ok": res["rc"] == 0, **res,
            "note": "剪映已重启并重新扫描草稿" if res["rc"] == 0 else "重启失败"}


skills.register(skills.Skill(
    id="ui_status", goal="读剪映当前状态（在首页还是编辑页、窗口多大）", channel="ui",
    args={}, sample={}, fn=skill_ui_status,
))
skills.register(skills.Skill(
    id="ui_press", goal="给剪映发一个白名单内的快捷键（分割/删除/撤销/播放等）", channel="ui",
    args={"keys": "例如 e、ctrl+z、space"}, sample={"keys": "ctrl+z"},
    required=["keys"], fn=skill_ui_press,
))
skills.register(skills.Skill(
    id="ui_open_draft", goal="在剪映首页打开指定草稿", channel="ui",
    args={"name": "草稿名（首页卡片名字）"}, sample={"name": "月子知识-成片"},
    required=["name"], fn=skill_ui_open_draft,
))
skills.register(skills.Skill(
    id="ui_export", goal="触发剪映导出（Ctrl+M，需在编辑页）", channel="ui",
    args={}, sample={}, fn=skill_ui_export,
))
skills.register(skills.Skill(
    id="ui_refresh", goal="重启剪映让它重新扫描草稿目录（重操作，需 confirm=yes）", channel="ui",
    args={"confirm": "必须传 yes 才执行"}, sample={"confirm": "yes"},
    required=["confirm"], fn=skill_ui_refresh,
))
