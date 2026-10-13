# 本机剪映执行衔接

目标是当前任务完成后返回可验证产物，不是展示一组成功截图就宣称无人值守系统已验收。

- 先读取 `computer-use` 技能，按其当前 API 和动作后观察规则操作。不要调用项目的旧 UI 桥接脚本来绕过该技能的控制通道。
- 从实时窗口清单识别剪映，使用真实返回的窗口对象。当前已观测运行程序在 `D:\软件\腾讯电脑管家软件搬家\软件搬家\剪映专业版\11.5.0.14471\JianyingPro.exe`，可能变动，优先复用实际窗口。
- 当前默认草稿目录曾显示为 `E:\陈锦卓\剪映\JianyingPro Drafts`，但 C 盘旧库的 AI CUT 草稿也已实际打开成功。按任务报告和首页卡片核对准确工程名，不能一律点第一张卡或擅自改变全局路径。
- 保存当前编辑状态后回首页，已在本机验证 `Ctrl+Alt+Q` 返回首页。新草稿可能要软件重启才扫描；不得在用户编辑未保存时强退。控件/窗口变化时重新观察。
- 打开 `host_task.project` 对应的唯一草稿，核对素材、片段、字幕、BGM、滤镜/特效是否真的在时间线上。灰色缩略图或缺失媒体需修复，不能照样导出。
- 用户要求直接成片时继续原生导出到本任务独立输出文件夹，关闭云端备份/发布选项，不覆盖已有输出。等导出成功后检查实际 MP4，而不是只凭按钮点击成功。
- 用户要求逐步放置效果时新建测试/任务工程，通过 UI 导入材料并放入时间线，验证轨道有真实片段后再分割/移动。拖拽无效不能当成功，也不能在后台写入工程冒充 UI 放置。
- 原生导出曾通过一条 4.55 秒素材的交互式测试；启动由用户协助。它不证明所有工程、资源、版本和跨软件无人值守导出成功。全强度光晕实测遮挡主体，字幕存在也不等于优质成片。
- 本 Skill 的软件操作由宿主模型及 computer-use 通道执行，CLI 返回的 `pending_host_execution` 是真实待完成状态。不能把这写成脚本自身已自动导出，也不能开启全局 `native_auto_render` 能力标志。

## 检查点与恢复（2026-10-12）

对新生成的 `aicut-host-task-2`，用 `scripts/host_task.py status|resume --task '<host_task>' --summary` 读取下一步。旧 v1 交接文件没有版本绑定和检查点，不能自动补成已验收。

每个实际完成的 UI 步骤后，把证据 JSON 写到本任务目录，再运行：

```powershell
& 'python' '<AI_CUT_ROOT>\scripts\host_task.py' record --task '<host_task>' --step '<next_step>' --evidence '<证据JSON>' --summary
```

证据字段：`project` 为精确工程名，`channel` 为 `computer-use`，`observation` 描述实际看到的结果，`screenshots` 为本次捕获图片绝对路径数组；打开工程时加 `editor_project`，其目录名必须等于工程名。截图及文字属于宿主观察记录，登记器不具备自动理解截图的能力，不能拿测试桩或无关图片登记真实动作。

顺序为打开工程 → 可选的可见放置/剪切 → 时间线与资源回读 → 可选的开始导出 → 导出成功 → 媒体核验。任何一步未满足就保留阻断，不跳过。

- `visible_placement_and_edit` 需要真实操作后的证据；后台工程生成不是证据。可见构建应使用同名唯一任务工程，按已有决策单的源区间和轨道逐步放置；不可往已完成时间线上重复添加素材充当过程。
- `verify_timeline_and_resources` 先在软件中保存；登记器回读实际 `editor_project`，核对片段、资源、关键帧和特效参数。非明文 JSON 可使用已配置、固定 SHA-256 的本机草稿读取器，在独立子进程解码输入副本，报告位于 `work/native-readback`；工具入口 `scripts/re_tools.py doctor|draft|pe|readback`。版本变化、解码失败返回 `encrypted_saved_draft_needs_host_verification`；解码后的字段默认值、帧对齐、关键帧或帧率证据仍未通过则继续阻断。不得改写原稿、提取密钥、以原始 `06-drafts` 副本冒充保存后的状态，或把解码成功称为剪辑验收成功。
- `start_native_export` 在点击最终导出前登记，证据中的 `output_path` 必须等于 `spec.output`，且该文件尚不存在。随后按该精确目录和文件名导出，关闭云端相关选项。
- `native_export` 在观察到成功后登记同一 `output_path`、`export_success_observed: true` 及成功画面。然后调用 `host_task.py verify-export --task '<host_task>' --summary`，校验保留路径的本次文件。
- 实际操作或检查失败要记录原因：`host_task.py fail --task '<host_task>' --step '<next_step>' --reason '<实际错误>' --summary`。检查器自身失败也会记账；同一步两次失败停止操作，诊断后再处理，不无限重试。
- `status/resume` 会校验方案、原工程、输入、已记录截图和 MP4 的 SHA-256；保存副本变动要重新通过语义回读。状态同时回填 `autopilot.json` 和 `status.json`，不会修改 MakerAPI 审批。
- 本轮原生技术核验完成仍是 `native_export_verified_pending_visual_review`；`ready_to_ship` 保持 false。还需观看成片，核对台词同步、标题重复、遮挡主体及动效连续性，不能用技术检查替代美观验收。
