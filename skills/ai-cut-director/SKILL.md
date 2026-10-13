---
name: ai-cut-director
description: 用 AI CUT 分析视频、生成剪辑方案并调用本地制作接口。适用于自主剪辑和出片请求；代码咨询不触发制作。剪映操作需要用户自备兼容组件与宿主界面工具。
---

# AI CUT Director

这份 Skill 是执行工作流，不是独立模型或常驻服务。先从用户上下文确定项目目录 `AI_CUT_ROOT`、Python 环境、素材和目标。不要假设本机盘符、模型、剪映版本或素材库存在。

在项目目录运行 `python scripts/deliver_edit.py --capabilities`。本公开版本排除了未核验的本机 JianYingDraft、采集库和剪映 DLL；剪映草稿组件缺失或 API 不匹配时明确报告，不能声称已经适配。可独立体验 `python demo/server.py`；其离线方案是预置演示。

用户给素材和明确制作目标后，沿既有素材理解、方案、质量门、制作接口和验收流程执行。默认保留原声，字幕按真实台词短语逐条显示，创建新工程。不要覆盖用户工程或使用旧决策单冒充本次模型理解。

```powershell
python scripts/autopilot.py --material '<本次素材绝对路径>' --goal '<用户制作指令>' --execute --copy-to-editor --summary
```

只有已安装并验证剪映组件且用户要求剪映时才用 `--copy-to-editor`。只要方案则省略 `--execute`；用户要求先审方案时等待确认。用户选择独立 FFmpeg MP4 时使用 `--mode mp4` 并省略 `--copy-to-editor`，不得把它称为剪映内制作。`--sheet` 仅用于用户指定已有方案或回归测试。

模型不可用、素材缺失、库为空、质量门失败时报告实际阻断。RAG 是检索上下文，不是训练。公开版不含采集库；规则兜底不能称为模型已学习或已完成理解。

如用户要求原生软件内出片，传入 `--host-deliverable mp4`。要求可见导入、放置和操作时加 `--visible-operations`。这些参数记录宿主任务要求，CLI 不会因此操作软件。读取 [宿主衔接参考](references/jianying-host.md)，使用目标环境提供的真实界面工具续接；无工具时停在等待软件执行，不能伪造完成。

中断后先用 `python scripts/host_task.py resume --task '<host-task.json绝对路径>' --summary` 找到下一步，避免重复制作。只登记真实观察；同一步失败两次保留工程并报告阻断。

完工报告区分生成工程、界面执行、原生导出和独立渲染。MP4 检查尺寸、时长、音轨、完整解码、代表性画面与字幕同步。技术检查不代表审美或内容事实已验收。外部上传、付费和账户操作需要各自授权。
