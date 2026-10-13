# Director Studio Demo

可独立运行的本机工作台。Python 3.10+、FFmpeg/ffprobe（含 libass）在 PATH 后：

```powershell
cd demo
python server.py
```

打开 http://127.0.0.1:8766 。离线模式使用原创 AI 咖啡影像和固定方案；确认后真实执行 FFmpeg、字幕渲染和完整解码。人工模式可导入自有视频并修改方案。固定方案不是实时模型生成，技术验收不等于审美验收。没有接入剪映执行组件。

独立 Demo 后端只使用 Python 标准库。重建演示动效另需 `numpy`、`opencv-python`。前端无 CDN。运行结果和导入素材保存在本机 `runtime/`，被 Git 忽略，使用者应自行管理留存。

## Optional cloud analysis

Cloud calls are disabled by default. Configure your own DashScope account and a model available in its region. Sampling sends video frames and the brief/transcript to that provider; it does not send the original audio. Explicit consent is required in the UI.

```powershell
$env:DASHSCOPE_API_KEY = '<your own key>'
$env:AICUT_DEMO_ALLOW_CLOUD = '1'
$env:AICUT_DEMO_MODEL = '<available vision model>'
python server.py
```

Do not put keys in source files. No credentials are bundled or automatically read from your desktop. Model quality and costs require your own validation. The loopback server is a development demo, without production authentication or remote deployment support.

## Tests

```powershell
python selftest_demo.py
python selftest_delivery.py
```

These tests render the bundled synthetic sample and check confirmation, protocol mocks, media decoding and review state. They do not validate a live API or autonomous editor operation.

## Asset provenance

`coffee-cinema-master.png` was generated on 2026-10-06 from an original coffee advertising prompt, without reference photos. `source.mp4` and `showcase.mp4` were generated using the included motion scripts and synthesized background audio. They are AI imagery with designed motion, not recorded customer footage or a live API editing result. The original contributor's rights in these assets are included under the repository license, to the extent such rights exist; no third-party trademark or likeness rights are granted.
