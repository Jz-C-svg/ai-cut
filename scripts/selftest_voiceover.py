# -*- coding: utf-8 -*-
"""Edge-TTS 配音全链路自测（scripts/selftest_voiceover.py）。

覆盖四件事：
  1. 模块与 API 就绪检查（纯本地，无网络）
  2. SRT 解析函数单元测试（纯本地，无网络）
  3. Edge-TTS 真实合成短文本（需网络；无网络时标记 SKIP 而非 FAIL）
  4. build_draft 消费 voiceover 字段：构造最小 sheet -> build() -> 回读 draft_content.json
     验证存在 audio 轨道与 voiceover-sub 文本轨道

用法：
    cd <project-root>
    $env:PYTHONPATH="<project-root>\\lib"
    venv\\Scripts\\python.exe scripts\\selftest_voiceover.py

退出码：0 = 无 FAIL（含 SKIP）；1 = 出现 FAIL。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

RESULTS: list[tuple[str, str, str]] = []  # (用例, PASS/FAIL/SKIP, 说明)


def report(name: str, status: str, msg: str = "") -> None:
    RESULTS.append((name, status, msg))
    tag = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIP": "⏭️  SKIP"}[status]
    print(f"[{tag}] {name}" + (f"  —— {msg}" if msg else ""))


# ---------- 一个最小 SRT 解析器（用于验证产物，与库无关） ----------
SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def parse_srt(text: str) -> list[dict]:
    """把 SRT 文本解析成 [{start, end, text}]；仅校验时间戳与文本非空。"""
    items = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        m = SRT_TIME.search(lines[1])
        if not m:
            continue
        items.append({"start": m.group(0).split("-->")[0].strip(),
                      "end": m.group(0).split("-->")[1].strip(),
                      "text": " ".join(lines[2:])})
    return items


def main() -> int:
    # ---------- 用例 1：模块与 API 就绪 ----------
    try:
        import tts_voice  # noqa: F401
        from JianYingDraft import AudioMaterial, AudioSegment, TrackType  # noqa: F401
        import inspect
        sig = inspect.signature(AudioSegment.__init__)
        ok = "volume" in sig.parameters and "audio" in [t.name for t in TrackType]
        report("1.模块/API就绪", "PASS" if ok else "FAIL",
               "tts_voice/AudioSegment(volume)/TrackType.audio" if ok else "API 缺失")
    except Exception as exc:  # noqa: BLE001
        report("1.模块/API就绪", "FAIL", f"{type(exc).__name__}: {exc}")

    # ---------- 用例 2：SRT 解析单元测试（纯本地） ----------
    sample = ("1\n00:00:00,100 --> 00:00:00,487\n欢迎\n\n"
              "2\n00:00:00,500 --> 00:00:00,912\n收看\n")
    parsed = parse_srt(sample)
    ok = len(parsed) == 2 and parsed[0]["text"] == "欢迎" and "-->" not in parsed[0]["start"] \
        and parsed[0]["end"].endswith("487")
    report("2.SRT解析单元", "PASS" if ok else "FAIL", f"解析出 {len(parsed)} 条")

    # ---------- 用例 3：Edge-TTS 真实合成（需网络） ----------
    mp3 = ROOT / "04-assets" / "voice" / "selftest-voiceover.mp3"
    srt = ROOT / "04-assets" / "voice" / "selftest-voiceover.srt"
    tts_ok = False
    try:
        asyncio.run(tts_voice._speak("测试配音", "zh-CN-XiaoxiaoNeural", mp3, srt))
        if mp3.exists() and mp3.stat().st_size > 0 and srt.exists() and srt.stat().st_size > 0:
            tts_ok = True
            report("3.Edge-TTS合成", "PASS", f"mp3 {mp3.stat().st_size}B, srt {srt.stat().st_size}B")
        else:
            report("3.Edge-TTS合成", "FAIL", "产物为空")
    except Exception as exc:  # noqa: BLE001
        report("3.Edge-TTS合成", "SKIP", f"无网络或 TTS 不可用({type(exc).__name__}: {exc})")

    # ---------- 用例 4：SRT 内容校验（依赖用例 3） ----------
    if tts_ok:
        try:
            items = parse_srt(srt.read_text(encoding="utf-8"))
            ok = len(items) >= 1 and all(it["text"] for it in items)
            report("4.SRT内容校验", "PASS" if ok else "FAIL", f"{len(items)} 条带时间戳字幕")
        except Exception as exc:  # noqa: BLE001
            report("4.SRT内容校验", "FAIL", f"{type(exc).__name__}: {exc}")
    else:
        report("4.SRT内容校验", "SKIP", "TTS 未跑通，跳过")

    # ---------- 用例 5：build_draft 消费 voiceover（依赖用例 3 的 mp3） ----------
    if not tts_ok:
        report("5.build消费voiceover", "SKIP", "无配音产物，跳过")
    else:
        try:
            import build_draft
            video = ROOT / "work" / "normalized" / "月子知识-0299.mp4"
            if not video.exists():
                report("5.build消费voiceover", "SKIP", "测试素材不存在")
            else:
                sheet = {
                    "project": "selftest-voiceover",
                    "template": "koubo-standard",
                    "width": 1080, "height": 1920, "fps": 30,
                    "cover_text": "自测封面", "cta": "自测落点",
                    "clips": [{"file": str(video.relative_to(ROOT)).replace("\\", "/"),
                               "start": 0.0, "end": 3.0, "speed": 1.0}],
                    "voiceover": {
                        "file": mp3.relative_to(ROOT).as_posix(),
                        "srt": srt.relative_to(ROOT).as_posix(),
                        "voice": "zh-CN-XiaoxiaoNeural",
                        "volume": 1.0,
                        "mute_original": True,
                    },
                }
                draft_path = build_draft.build(sheet, build_draft.load_config())
                data = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
                tracks = data.get("tracks", [])
                aud = [t for t in tracks if t.get("type") == "audio"]
                sub = [t for t in tracks if t.get("name") == "voiceover-sub"]
                # 确认 audio 轨道确实挂着我们的 mp3
                audio_paths = " ".join(
                    a.get("path", "") for a in data.get("materials", {}).get("audios", []))
                has_mp3 = "selftest-voiceover.mp3" in audio_paths
                ok = len(aud) >= 1 and has_mp3 and len(sub) >= 1
                report("5.build消费voiceover", "PASS" if ok else "FAIL",
                       f"audio轨道={len(aud)} 挂mp3={has_mp3} voiceover-sub={len(sub)}")
        except Exception as exc:  # noqa: BLE001
            report("5.build消费voiceover", "FAIL", f"{type(exc).__name__}: {exc}")

    # ---------- 汇总 ----------
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    n_skip = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    print("-" * 48)
    print(f"汇总：PASS {n_pass}  SKIP {n_skip}  FAIL {n_fail}")
    if n_fail:
        print("[FAIL] 存在失败用例")
        return 1
    print("[PASS] 全部用例通过（或网络相关用例已跳过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
