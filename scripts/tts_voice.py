# -*- coding: utf-8 -*-
r"""免费 AI 配音（Edge-TTS，微软在线 TTS，无需 API Key）

用法：
  python tts_voice.py --text "欢迎收看月子知识课堂" --out 04-assets\voice\demo.mp3
  python tts_voice.py --file 稿.txt --voice zh-CN-YunxiNeural --srt out.srt
  python tts_voice.py --text "..." --list-voices        # 列出中文声音

输出：MP3（默认 04-assets\voice\）+ 可选 SRT 字幕（word boundary 对齐）。
此功能会将待合成文本发送到微软在线 TTS 服务。服务条款、可用性与使用限制以服务提供方当前政策为准；不要用它处理不宜上传的文本。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

ZH_VOICES = [
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunjianNeural",
    "zh-CN-YunxiNeural", "zh-CN-YunxiaNeural", "zh-CN-YunyangNeural",
    "zh-CN-liaoning-XiaobeiNeural", "zh-CN-shaanxi-XiaoniNeural",
]


def _voices() -> list[str]:
    import edge_tts
    return sorted(v["ShortName"] for v in asyncio.run(edge_tts.list_voices()))


async def _speak(text: str, voice: str, out: Path, srt: Path | None) -> None:
    import edge_tts
    out.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text, voice, rate="+0%", volume="+0%",
                                       boundary="WordBoundary")
    if srt is not None:
        submaker = edge_tts.SubMaker()
        with out.open("wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    submaker.feed(chunk)
        srt.write_text(submaker.get_srt(), encoding="utf-8")
        print(f"  [字幕] {srt}")
    else:
        await communicate.save(str(out))
    print(f"  [配音] {out}  voice={voice}")


def main() -> int:
    ap = argparse.ArgumentParser(description="免费 AI 配音（Edge-TTS）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", help="要朗读的文本")
    src.add_argument("--file", help="文本文件（UTF-8）")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help="声音（默认 XiaoxiaoNeural）")
    ap.add_argument("--out", help="输出 mp3 路径（默认 04-assets/voice/tts.mp3）")
    ap.add_argument("--srt", help="可选：输出 SRT 字幕路径（word boundary 对齐）")
    ap.add_argument("--list-voices", action="store_true", help="列出全部中文声音后退出")
    args = ap.parse_args()

    if args.list_voices:
        for v in ZH_VOICES:
            print(v)
        return 0

    text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
    if not text.strip():
        print("❌ 文本为空")
        return 1

    out = Path(args.out) if args.out else ROOT / "04-assets" / "voice" / "tts.mp3"
    srt = Path(args.srt) if args.srt else None
    try:
        asyncio.run(_speak(text.strip(), args.voice, out, srt))
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 配音失败: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
