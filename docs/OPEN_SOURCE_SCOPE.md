# 开源范围 / Open-source scope

此仓库是从开发工作副本整理的源码发布。它不等于原机器的全部文件，也不继承原机器的账号、软件安装、模型、素材库或商业交付承诺。

## 包含

- 经整理的 AI CUT 原创 Python 源码、接口、CLI、GUI 与工作流支持文件。
- 不带个人路径或密钥的配置与示例，以及当前状态说明。
- 独立 Demo 与注明来源的原创 AI 咖啡演示影像。它们是合成演示内容，不是客户素材或真实云端剪辑验证。
- 保留原许可与署名的、明确标注的第三方参考文件（如实际包含）。外部依赖及参考不自动成为 AI CUT 原创代码。

最终以 Git 仓库中实际跟踪的文件为准；文档提到的功能、目录或历史产物并不表示它们都随发布附带。

## 排除

- 私人素材、剪辑成片、用户转写、截图、原生草稿、运行日志与本机测试证据。
- 账号、口令、API 密钥、浏览器会话、身份和收款资料及私人配置。
- 采集的视频、图片、数据集、向量库和个人记忆数据库；RAG 代码开放不表示原采集内容可转授权。
- 模型权重、虚拟环境、依赖安装目录、已打包程序和安装包。
- 剪映 / CapCut 等第三方应用及其 DLL、专有特效、滤镜、字体、音频与其它资源。
- 未核验来源和再分发权利的本机 `JianYingDraft` 副本与其它第三方源码。
- 商品交付 ZIP、客户数据、销售监测配置、合同和商业交易文档。

## 许可与运行边界

AI CUT 原创代码和文档保留原项目的 Apache-2.0 许可声明。Copyright 2026 AI CUT contributors。第三方组件遵循各自许可，本仓库不能授予它们或你导入素材的额外权利。

用户需自行安装兼容依赖、模型和编辑器，配置素材路径及模型服务。剪映草稿写入需要外部 `JianYingDraft`；主项目 portable 路径目前也间接依赖该排除组件，仍需解耦适配，不能称为这份副本已打通的独立渲染入口。独立 Demo 使用 Python 标准库与外部 FFmpeg，可优先体验；其 6 项自测通过不等于真实云端或剪映自主操作已验收。受版本约束的回读只处理用户选择的本地工程，不能作为通用编辑器解码或新版本兼容承诺。当前完整可见自主出片、新机部署与美学验收仍未完成。

开源代码不代表剪映 / CapCut 厂商授权或官方集成，也不包含任何原商品交易、独家转让、售后或交付期限承诺。

## English

This is a curated source release, not a copy of the entire development machine. Tracked repository files define what is included. References to historical outputs, folders or capabilities do not mean those artifacts are distributed.

Included material consists of curated original source, interfaces, workflows, sanitized examples/configuration, documentation, and the standalone demo's original synthetic coffee imagery with provenance. These synthetic assets are not customer footage or evidence of live cloud editing. Any explicitly included third-party references retain their own notices and licenses.

Excluded: private footage, renders, transcripts, screenshots, drafts, runtime evidence and logs; credentials, sessions, personal and payout data; collected datasets, vector stores and personal memory; model weights, environments and packaged binaries; editor applications, DLLs and proprietary resources; unverified local `JianYingDraft` or other third-party copies; commercial delivery archives, contracts, customer data and sale-monitor settings.

Original project code and documentation retain Apache-2.0, Copyright 2026 AI CUT contributors. Third-party components and imported media retain their own rights. Users must install compatible dependencies, models and editors separately. Jianying writing requires external `JianYingDraft`; the main portable path indirectly depends on that excluded component too and still needs decoupling. It is not an independently accepted rendering entry point in this public copy. The standalone demo uses Python's standard library with external FFmpeg and passed six checks; these do not validate live cloud inference or autonomous editor operation. Version-bound readback is not a universal decoder or a promise of future compatibility. Complete visible autonomous output, clean-machine deployment and aesthetic acceptance remain unverified.

This source release is not an official editor integration and carries no former product-sale, exclusive-transfer, support or delivery-time commitment.
