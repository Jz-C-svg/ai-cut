r"""模型预热/保活工具（v4.0.0 · 上线体验关键）。

背景实测：gemma4-26b 在 CPU 上首次加载约 211s，单次推理可达 300s；
换 Qwen2.5-VL 7B 后加载/推理快 3-4 倍。无论哪个模型，**首次加载都慢**，
本工具用于：
  · 应用启动时（GUI 环境向导/命令行 --warmup）主动预热当前默认视觉模型，
    把"首载等待"从用户剪辑时转移到启动时；
  · 预热后 keep_alive 保活，热调用可到秒级。

用法：
  python scripts\warmup.py            # 预热当前默认视觉模型（get_vision_model）
  python scripts\warmup.py --text     # 顺带预热文本模型（gemma4）
  python scripts\warmup.py --status   # 只检查不预热
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from aicut.ollama_endpoint import ollama_url


def _post(url: str, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ollama_up() -> bool:
    try:
        req = urllib.request.Request(ollama_url('/api/tags'))
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:  # noqa: BLE001
        return False


def warm_model(model: str, prompt: str = "回复两个字：就绪", timeout: int = 900) -> float:
    """调用一次最小请求，把模型加载进内存并 keep_alive。返回耗时秒。"""
    started = time.time()
    _post(ollama_url('/api/chat'), {
        "model": model, "messages": [{"role": "user", "content": prompt}],
        "stream": False, "think": False, "keep_alive": "30m",
        "options": {"num_predict": 8},
    }, timeout=timeout)
    return time.time() - started


def main() -> int:
    if not _ollama_up():
        print("[warmup] Ollama 未启动。请先运行 ollama serve 或从 GUI 设置页启动。")
        return 1

    args = sys.argv[1:]
    if "--status" in args:
        from aicut.llm import get_vision_model
        print(f"[warmup] 当前默认视觉模型: {get_vision_model()}")
        try:
            with urllib.request.urlopen(ollama_url('/api/ps'), timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
            loaded = [m.get("name") for m in data.get("models", [])]
            print(f"[warmup] 已驻留模型: {loaded or '无（首次调用需加载）'}")
        except Exception as exc:  # noqa: BLE001
            print(f"[warmup] 查询驻留失败: {exc}")
        return 0

    from aicut.llm import get_vision_model, OLLAMA_MODEL
    vision = get_vision_model()
    print(f"[warmup] 预热视觉模型: {vision}（首次加载可能需要数分钟）...")
    try:
        t = warm_model(vision)
        print(f"[warmup] ✓ 视觉模型就绪（{t:.1f}s），已保活 30 分钟")
    except Exception as exc:  # noqa: BLE001
        print(f"[warmup] ✗ 视觉模型预热失败: {type(exc).__name__} {exc}")

    if "--text" in args:
        print(f"[warmup] 预热文本模型: {OLLAMA_MODEL} ...")
        try:
            t = warm_model(OLLAMA_MODEL)
            print(f"[warmup] ✓ 文本模型就绪（{t:.1f}s）")
        except Exception as exc:  # noqa: BLE001
            print(f"[warmup] ✗ 文本模型预热失败: {type(exc).__name__} {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
