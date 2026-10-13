r"""AI 配音链路（v3.3 P0-1，框架先行）。

对照 jy-skills 的"合成 AI 配音"能力，本模块定义**统一接口**，但**不引入任何付费 API**：
  - 首选：本地/免费 TTS（edge-tts：免费、无需 Key，仅走微软公开语音服务；
          或本机 Windows SAPI 系统语音）。
  - 当前 venv 未装 edge_tts / pyttsx3 / pywin32 —— 运行时探测，缺依赖时返回
    ``status="待接入本地 TTS"``，把接口设计与接入步骤一并带回，不报错中断。

设计：
  generate_voiceover(text, voice_preset, output_path) -> dict
    返回 {status, output_path, duration?, provider?, message?}。
  status ∈ {"ok", "待接入本地 TTS", "失败"}。

接入顺序（接入本地 TTS 时按此尝试，谁可用用谁）：
  1. edge-tts（pip install edge-tts）：在线但免费、无 Key，音质最好；
  2. Windows SAPI（PowerShell System.Speech）：纯本地、零依赖，但音质机械；
  3. 本地神经网络 TTS（如 piper / sherpa-onnx，素材不出机）——后续候选。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .paths import WORK, ffmpeg

# 默认音色预设（edge-tts 的 voice 名；接入后可在此扩展口播常用音色）
VOICE_PRESETS = {
    "口播女声": "zh-CN-XiaoxiaoNeural",
    "口播男声": "zh-CN-YunxiNeural",
    "温柔女声": "zh-CN-XiaoyiNeural",
    "沉稳男声": "zh-CN-YunjianNeural",
}


def _have_edge_tts() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _have_sapi() -> bool:
    # Windows 自带 System.Speech，PowerShell 可调，无需 Python 依赖
    return shutil.which("powershell.exe") is not None


def _resolve_voice(voice_preset: str) -> str:
    return VOICE_PRESETS.get(voice_preset, voice_preset or VOICE_PRESETS["口播女声"])


def _edge_tts_synth(text: str, voice: str, output_path: Path) -> None:
    """调用 edge-tts（免费、无 Key）。"""
    import asyncio

    import edge_tts

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))

    asyncio.run(_run())


def _sapi_synth(text: str, output_path: Path) -> None:
    """调用 Windows SAPI（纯本地）输出 WAV。"""
    # SAPI 只写 WAV；mp3 由 ffmpeg 转（分析用途之外的成片配音，写素材文件允许）
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{str(output_path).replace(chr(39), chr(39)*2)}');"
        f"$s.Speak('{text.replace(chr(39), '')}');"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")


def generate_voiceover(text: str, voice_preset: str = "口播女声",
                       output_path: str | Path | None = None) -> dict:
    r"""生成一段口播配音（框架）。

    Args:
        text: 要朗读的文本。
        voice_preset: 音色预设名（见 VOICE_PRESETS），或直接给 edge-tts voice 名。
        output_path: 输出音频路径（.mp3/.wav）；None 时落到 work/dubbing/。

    Returns:
        ``{status, output_path, provider, voice, message}``。
        未接入本地 TTS 时 ``status="待接入本地 TTS"``，不抛异常。
    """
    text = (text or "").strip()
    if not text:
        return {"status": "失败", "error": "文本为空"}

    voice = _resolve_voice(voice_preset)
    if output_path is None:
        out_dir = WORK / "dubbing"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"vo-{abs(hash(text)) & 0xffffff:06x}.mp3"
    output_path = Path(output_path)

    # 1) edge-tts（免费无 Key，首选音质）
    if _have_edge_tts():
        try:
            _edge_tts_synth(text, voice, output_path)
            if output_path.exists() and output_path.stat().st_size > 0:
                return {"status": "ok", "provider": "edge-tts", "voice": voice,
                        "output_path": str(output_path),
                        "message": "已用 edge-tts（免费、无 Key）合成"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "失败", "provider": "edge-tts",
                    "error": f"{type(exc).__name__}: {exc}"[:200]}

    # 2) Windows SAPI（纯本地、零依赖兜底）
    if _have_sapi():
        try:
            wav = output_path.with_suffix(".wav")
            _sapi_synth(text, wav)
            if wav.exists() and wav.stat().st_size > 0:
                # 若是 .mp3 请求，ffmpeg 转一下；否则直接用 wav
                if output_path.suffix.lower() == ".mp3":
                    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error",
                                    "-y", "-i", str(wav), str(output_path)],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace")
                    if output_path.exists() and output_path.stat().st_size > 0:
                        wav.unlink(missing_ok=True)
                        return {"status": "ok", "provider": "sapi-local",
                                "voice": "系统默认语音", "output_path": str(output_path),
                                "message": "已用 Windows SAPI 本地合成"}
                return {"status": "ok", "provider": "sapi-local",
                        "voice": "系统默认语音", "output_path": str(wav),
                        "message": "已用 Windows SAPI 本地合成（WAV）"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "失败", "provider": "sapi-local",
                    "error": f"{type(exc).__name__}: {exc}"[:200]}

    # 3) 都不可用：记档待接入
    return {
        "status": "待接入本地 TTS",
        "output_path": str(output_path),
        "voice_preset": voice_preset,
        "voice": voice,
        "message": (
            "当前环境未装本地 TTS（edge-tts / pyttsx3 / SAPI 均不可用）。"
            "接入步骤：① pip install edge-tts（免费、无 Key，推荐）；"
            "② 或用 Windows SAPI（已内置，PowerShell System.Speech）；"
            "③ 后续候选 piper / sherpa-onnx 纯本地神经网络 TTS。"
            "接口已就绪：generate_voiceover(text, voice_preset, output_path)，"
            "装好任一 TTS 后无需改调用方即可生效。"
        ),
    }


def list_voice_presets() -> dict:
    """列出可用音色预设。"""
    return dict(VOICE_PRESETS)
