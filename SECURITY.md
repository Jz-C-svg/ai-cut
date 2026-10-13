# Security / 安全说明

AI CUT 运行在本机，可能读取素材、转写文本和草稿，并按所选制作请求写入输出。启用外部模型、联网研究或在线语音服务时，会按功能发送数据。只处理你有权使用的素材；先备份需要保留的工程。

不要在公开 Issue、PR 或附件中提交密钥、会话令牌、身份和收款信息、私人视频或完整运行日志。报告前检查日志和报告中的素材路径、台词、任务内容与 API 请求。

如发现凭据泄露、任意文件写入、命令执行或数据意外外发：

- 停止受影响流程并保留脱敏复现。
- 涉及已泄露凭据时，先在对应服务撤销或轮换。
- 如仓库启用了 GitHub 私密漏洞报告，使用该入口；否则仅发布不含利用细节和私人信息的简短 Issue，请维护者安排私密联系。

本项目没有承诺固定安全响应时限或受支持的生产版本。原生 DLL 回读属于可选、版本受约束的本地功能，不随仓库分发编辑器 DLL，不应附加进程、提取密钥或覆盖原工程。

## English

AI CUT can read local media, transcripts and drafts, and write the outputs selected in a production request. Enabled external models, research and speech services transmit data required by those features. Back up valuable projects before using automation.

Never include credentials, tokens, identity or payout data, private footage, or unreviewed logs in public issues or pull requests. Reports can contain media paths, transcripts, task details and API data.

For leaked credentials, arbitrary file writes, command execution or unexpected disclosure, stop the affected flow and preserve a sanitized reproduction. Revoke exposed credentials at the provider. Use GitHub private vulnerability reporting if enabled; otherwise open a minimal issue without sensitive details to arrange private contact.

No fixed security response time or production support policy is promised. Optional native DLL readback is local and version-bound. Editor binaries are not bundled; do not attach to processes, extract keys, or replace source projects.
