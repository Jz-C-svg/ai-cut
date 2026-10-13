# AI CUT

[English](README.en.md) · [开源范围](docs/OPEN_SOURCE_SCOPE.md) · [贡献指南](CONTRIBUTING.md)

AI CUT 是一个面向 Windows 的早期 AI 辅助视频剪辑开发项目。它把素材解析、转写、剪辑决策、制作接口和产物检查串起来，让模型提出剪辑方案，再由明确的制作请求执行。

**当前开放的是可继续开发的源码，不是已经完成验收的一键全自动剪辑软件。** 完整的新机部署、软件内可见自主剪辑与导出，以及成片美学质量仍需验证。不要把工程生成成功当成剪映已经执行，或把可解码 MP4 当成视觉质量达标。

## 先体验独立 Demo

独立工作台仅需 Python 标准库，以及在 `PATH` 中的 FFmpeg / ffprobe（FFmpeg 需包含 libass）。在仓库根目录运行：

```powershell
python demo/server.py
```

打开 <http://127.0.0.1:8766>。离线模式用原创 AI 咖啡影像和固定方案展示确认、FFmpeg 渲染与完整解码；可换成自己的素材和手工方案。**固定方案不是本次模型生成，Demo 没有操作剪映。** 云端分析默认关闭，需使用者自有密钥并明确同意发送抽帧和文本；真实 API 效果与费用需自行验证。详见 [Demo 运行与素材来源](demo/README.md)。

本次独立 Demo 的 6 项自测通过；这不覆盖真实云端模型或自主编辑器操作。它是这份开源副本优先提供的可运行入口。

## 工作流

```text
自己的素材 → ASR / 画面分析 → 剪辑决策单 → 制作前检查
                                           ↓ 用户确认
                               剪映草稿 / FFmpeg MP4
                                           ↓
                             文件回读、技术检查、人工看片
```

这是主项目的设计流程。开源副本中部分制作路径依赖未分发的组件，具体限制见下表。

- Python CLI、SDK 与 MCP 共用制作接口，区分方案检查和实际执行。
- 支持记录方案、输入文件哈希、可恢复宿主任务及导出检查状态。
- 特效、滤镜、字幕与关键帧具有结构化执行和回读校验逻辑；资源是否存在、编辑器版本和视觉效果需另行核对。
- 规则或检索内容帮助规划；检索增强（RAG）不等于训练或微调模型。本仓库不提供原工作副本中的采集数据库。

## 支持状态

| 路径 | 当前范围 | 尚未完成的验证 |
| --- | --- | --- |
| 独立 Demo | 无 Python 第三方依赖的本机工作台；固定/手工方案，确认后调用 FFmpeg | 真实云端效果、生产部署与美学验收 |
| 剪映专业版 | 生成草稿、制作请求、宿主步骤与回读检查；草稿写入依赖外部 `JianYingDraft` | 干净环境兼容；完整可见操作到原生导出；美学验收 |
| CapCut | 存在草稿适配代码 | 未完成实机验收，不承诺与剪映相同 |
| Premiere / DaVinci Resolve / Final Cut / VEGAS / After Effects | 存在桥接或接口代码 | 当前生产入口拦截未验收的原生交付 |
| 主项目 portable / FFmpeg MP4 | 保留渲染与技术检查代码 | 当前仍间接导入依赖 `JianYingDraft` 的 `build_draft`，未随副本提供该组件，端到端阻断；待解耦适配。独立 Demo 不受此依赖影响 |
| 原生工程只读分析 | PE 静态分析、草稿结构探测、受版本约束的本地回读 | 无通用解码器保证；默认字段、帧量化与曲线语义仍有兼容差异 |

宿主任务会返回下一待操作步骤并接收真实观察记录；脚本生成任务本身不会证明屏幕上的导入、放置、剪切或导出已经发生。缺少依赖、模型不可用、方案不适配或检查失败时，应保留阻断状态。

## 源码开始

在仓库根目录使用 PowerShell。建议 Python 3.12；编辑器自动化以 Windows 为目标。需要分别安装 FFmpeg / ffprobe，并确保在 `PATH` 中可调用。模型权重、剪映、FFmpeg 可执行文件及 `JianYingDraft` 不随仓库提供。

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# 查看制作工具的参数结构；不读取素材、不调用模型、不出片
.\venv\Scripts\python.exe -X utf8 scripts\maker_api.py tools
```

按你的环境编辑 `config/aicut.json`：FFmpeg 路径、ASR、模型后端、剪映草稿位置等。Python 子进程默认使用当前运行的 Python，配置项 `python` 可以覆盖。不要粘贴已有工作机器的私人绝对路径或凭据。按所需模块检查 `requirements-optional.txt`，基础安装不包含全部转写、视觉、GUI 与自动化依赖。

剪映草稿路径还需要自行取得并安装兼容的 `JianYingDraft`，确保当前虚拟环境能 `import JianYingDraft`；本仓库没有随附或重新授权该依赖，也没有承诺任意同名包都兼容。

下面是主项目开发用调用方式，需先补齐兼容的外部依赖、素材路径和可用模型；未补齐时会阻断，不能当成开箱出片示例：

```powershell
.\venv\Scripts\python.exe -X utf8 scripts\agent.py --material "<自己的素材.mp4>" --goal "保留原话，按台词短语分段字幕，生成口播剪辑方案" --software jianying --engine project

# 审阅上一步返回的方案、输入与目标后，再提供对应请求 ID 和确认哈希
.\venv\Scripts\python.exe -X utf8 scripts\maker_api.py execute --request "<request_id>" --approve-request-sha256 "<request_sha256>"
```

`portable` 是主项目 FFmpeg 路径的接口选项，但当前存在上述 `JianYingDraft` 间接依赖，尚未在这份开源副本中独立端到端打通。需要立即体验渲染时使用独立 Demo。`--copy-to-editor` 会请求复制到编辑器草稿库，应在方案检查阶段明确选择并备份原草稿。已有决策单可以通过 `scripts/maker_api.py inspect --sheet "<方案.sheet.json>"` 请求检查；它仍受对应路径依赖约束，也不代表执行了新的模型分析。

需要外部 Agent 接入时，`scripts/mcp_server.py` 提供 stdio MCP 入口；具体宿主如何运行、批准本地工具和操作窗口，由宿主负责。Skill 是工作流指令，不能代替已运行的模型或桌面自动化权限。

## 模型与隐私

本地转写和本地模型可组成离线处理路径，但首次安装依赖和下载模型通常需要网络。云端后端启用后，可能发送文本、抽帧图片或所选功能需要的媒体；联网研究会发送查询，Edge-TTS 会发送待合成文本。使用前检查实际后端和功能，敏感素材不要交给未经批准的服务。

API 密钥使用环境变量与配置占位符，不提交 `.env`、本机配置、日志或输出。请审查运行期间生成的报告：它们可能包含素材路径、台词和任务内容。

## 开发与验证

针对改动运行相关 `scripts/selftest_*.py`。示例：

```powershell
.\venv\Scripts\python.exe -X utf8 scripts\selftest_host_tasks.py
.\venv\Scripts\python.exe -X utf8 scripts\selftest_re_tools.py
```

从仓库根目录运行。部分历史测试依赖未分发的素材、目录、编辑器资源或第三方库；不能把原机器的测试成绩当成这份开源副本的新机验收。提交问题时写明编辑器、Python、模型及 FFmpeg 版本，并提供脱敏复现。

## 许可

项目原创代码和文档采用 [Apache-2.0](LICENSE)，Copyright 2026 AI CUT contributors。第三方组件保持各自许可；剪映 / CapCut 软件、商标与专有资源不在本许可范围。本项目与其厂商没有隶属关系。可分发内容和排除内容见 [开源范围](docs/OPEN_SOURCE_SCOPE.md)。
