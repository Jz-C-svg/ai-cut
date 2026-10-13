# Contributing / 贡献指南

欢迎改进 AI CUT。请先阅读 README 的支持状态和 [开源范围](docs/OPEN_SOURCE_SCOPE.md)。

1. 描述具体问题与复现步骤，写明操作系统、Python、剪辑软件、FFmpeg 与模型版本。
2. 使用你有权公开的最小合成素材或自制素材。删除日志中的台词、私人路径、账号、密钥和个人信息。
3. 保持变更范围清晰，运行相关自测。将隔离测试、真实模型调用、编辑器实测和人工画面审查分别报告。
4. 新的软件适配必须通过真实软件打开、保存、回读和导出验证，才能修改支持矩阵；不要以占位文件或测试替身宣称原生出片成功。
5. 保留失败与待验证状态。不得通过伪造 UI 观察、补填缺失证据或无依据放宽容差让验收通过。

优先欢迎：可复现的新机配置、版本化草稿兼容、字幕时序、素材定位、可见操作衔接、导出校验和成片质量评审。模型服务和联网功能应明确告知发送内容，并允许使用者选择本地处理。

只提交你有权贡献的代码与资产。保留第三方版权、许可和来源，不提交抓取素材库、编辑器 DLL、模型权重、私人草稿或商业交易文档。除明确另有说明外，有意提交并纳入本项目的贡献依照 Apache-2.0 许可处理。

## English

Before contributing, read the support matrix and [scope](docs/OPEN_SOURCE_SCOPE.md). Provide a small reproduction and your OS, Python, editor, FFmpeg and model versions. Use synthetic or self-created media you may publish; remove private paths, transcripts, credentials and personal data.

Keep changes focused and run the relevant checks. Report isolated tests, live inference, real-editor tests, and visual review separately. New native adapters need real open/save/readback/export evidence before the support matrix changes. Preserve blocked or pending states; never fabricate UI observations or relax tolerances without evidence.

Contribute only material you have permission to share and retain third-party notices. Do not submit collected media databases, editor DLLs, weights, private drafts or commercial transaction documents. Unless explicitly stated otherwise, contributions intentionally submitted and accepted into this project are under Apache-2.0.
