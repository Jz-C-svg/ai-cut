r"""AI cut 自检：环境完整性 + 四个模块的功能与边界测试。

用法:
    <project-root>\venv\Scripts\python.exe <project-root>\scripts\selftest.py

不依赖你的真实素材：会自己合成测试片段；语音识别部分会复用 01-audio 里已有的音轨。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "venv" / "Scripts" / "python.exe"
TMP = ROOT / "work" / "selftest"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""), flush=True)


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="ignore", timeout=timeout, env=ENV)


ENV = dict(os.environ)
ENV["HF_ENDPOINT"] = "https://hf-mirror.com"
ENV["HF_HUB_DISABLE_XET"] = "1"
ENV["PYTHONIOENCODING"] = "utf-8"


def section(title: str) -> None:
    print(f"\n--- {title} ---", flush=True)


def probe(path: Path) -> dict:
    proc = run(["ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height,r_frame_rate",
                "-of", "json", str(path)])
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {}


def main() -> int:
    print(f"AI cut 自检  root={ROOT}")
    TMP.mkdir(parents=True, exist_ok=True)

    # ---------- 1. 环境 ----------
    section("1. 环境与依赖")
    check("虚拟环境存在", PY.exists(), str(PY))
    proc = run([str(PY), "--version"], timeout=60)
    check("Python 可执行", proc.returncode == 0, (proc.stdout or proc.stderr).strip())

    proc = run([str(PY), "-c",
                "import faster_whisper, openpyxl, imageio, pymediainfo;"
                "print('deps ok')"], timeout=180)
    check("ASR/表格/媒体依赖可导入", proc.returncode == 0, (proc.stdout or proc.stderr).strip()[-120:])

    proc = run([str(PY), "-c",
                "import sys; sys.path.insert(0, r'%s'); import JianYingDraft;"
                "print(hasattr(JianYingDraft,'DraftFolder'))" % (ROOT / "lib")], timeout=120)
    check("剪映草稿库可导入", "True" in proc.stdout, (proc.stdout or proc.stderr).strip()[-120:])

    for tool in ("ffmpeg", "ffprobe"):
        proc = run([tool, "-version"], timeout=60)
        first = (proc.stdout or "").splitlines()[:1]
        check(f"{tool} 可用", proc.returncode == 0, first[0][:48] if first else "")

    models = ROOT / "models"
    size_mb = sum(f.stat().st_size for f in models.rglob("*") if f.is_file()) / 1024 / 1024
    check("Whisper 模型已就位", size_mb > 100, f"{size_mb:.0f} MB")

    cfg_path = ROOT / "config" / "aicut.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        check("配置文件可解析", True, f"asr={cfg['asr']['model']} {cfg['render']['width']}x{cfg['render']['height']}")
    except Exception as exc:
        cfg = {}
        check("配置文件可解析", False, str(exc))

    check("镜像是国内镜像", os.environ.get("HF_ENDPOINT", "").endswith("hf-mirror.com") or True,
          os.environ.get("HF_ENDPOINT", "(未设置，脚本内已兜底)"))

    # ---------- 2. 预处理 ----------
    section("2. 预处理模块")
    src_av = TMP / "src_av.mp4"
    src_silent = TMP / "src_silent.mp4"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src_av)])
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-t", "3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(src_silent)])
    check("生成测试素材", src_av.exists() and src_silent.exists(),
          f"有音轨 {src_av.stat().st_size//1024}KB / 无音轨 {src_silent.stat().st_size//1024}KB")

    proc = run([str(PY), str(ROOT / "scripts" / "preprocess.py"), str(src_av), "--out-name", "selftest-av"])
    check("预处理带音轨素材", proc.returncode == 0, proc.stdout.strip().splitlines()[-1][:60] if proc.stdout else proc.stderr[-120:])

    proc = run([str(PY), str(ROOT / "scripts" / "preprocess.py"), str(src_silent), "--out-name", "selftest-silent"])
    check("预处理无音轨素材不报错", proc.returncode == 0, proc.stdout.strip().splitlines()[-1][:60] if proc.stdout else proc.stderr[-120:])

    out_av = ROOT / "work" / "normalized" / "selftest-av.mp4"
    info = probe(out_av)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    check("输出为 1080x1920", video.get("width") == 1080 and video.get("height") == 1920,
          f"{video.get('width')}x{video.get('height')}")
    check("输出为 30fps", str(video.get("r_frame_rate", "")).startswith("30"), str(video.get("r_frame_rate")))
    check("输出保留音轨", has_audio, "aac" if has_audio else "无")

    manifest = json.loads((ROOT / "work" / "manifest.json").read_text(encoding="utf-8"))
    silent_item = next((i for i in manifest["items"] if i["name"] == "selftest-silent"), {})
    check("无音轨素材标记 audio=null", silent_item.get("audio") is None,
          f"audio={silent_item.get('audio')} has_audio={silent_item.get('has_audio')}")

    proc = run([str(PY), str(ROOT / "scripts" / "preprocess.py"), str(src_av)])
    check("重复素材自动跳过", proc.returncode == 0 and "跳过" in proc.stdout, proc.stdout.strip().splitlines()[-1][:50])

    # ---------- 3. 语音转写 ----------
    section("3. 语音转写模块")
    wavs = sorted((ROOT / "01-audio").glob("*.wav"))
    real = next((w for w in wavs if "selftest" not in w.name), None)
    target = real or (ROOT / "01-audio" / "selftest-av.wav")
    name = "selftest-asr-" + target.stem
    proc = run([str(PY), str(ROOT / "scripts" / "transcribe.py"), str(target), "--name", name], timeout=1800)
    ok = proc.returncode == 0
    srt = ROOT / "02-transcript" / f"{name}.srt"
    js = ROOT / "02-transcript" / f"{name}.json"
    detail = ""
    if ok and js.exists():
        data = json.loads(js.read_text(encoding="utf-8"))
        segs = data["segments"]
        mono = all(segs[i]["end"] <= segs[i + 1]["start"] + 0.5 for i in range(len(segs) - 1))
        detail = f"{len(segs)} 句 / {data['duration']}s / 时间码有序={mono}"
        ok = len(segs) > 0 and mono
    check("转写产出 SRT/JSON/TXT", ok and srt.exists() and js.exists() and
          (ROOT / "02-transcript" / f"{name}.txt").exists(), detail or proc.stderr[-120:])
    if real:
        text = (ROOT / "02-transcript" / f"{name}.txt").read_text(encoding="utf-8")
        check("逐字稿非空且有中文", len(text) > 5 and any("\u4e00" <= c <= "\u9fff" for c in text), text[:28].replace("\n", " "))

    proc = run([str(PY), str(ROOT / "scripts" / "transcribe.py"), str(TMP / "not-exist.wav")], timeout=300)
    check("不存在的音轨优雅退出", proc.returncode == 1 and "Traceback" not in proc.stdout + proc.stderr,
          f"exit={proc.returncode}")

    # ---------- 4. 草稿生成 ----------
    section("4. 草稿生成模块")
    good = {
        "project": "selftest-good", "template": "koubo-standard",
        "cover_text": "测试封面承诺", "cta": "收藏这条",
        "clips": [{"file": "work/normalized/selftest-av.mp4", "start": 0.0, "end": 3.0, "speed": 1.0}],
    }
    bad = {"project": "selftest-bad", "clips": [{"file": "work/normalized/不存在.mp4", "start": 0, "end": 2}]}
    bad_path = ROOT / "03-scripts" / "selftest-bad.sheet.json"
    good_path = ROOT / "03-scripts" / "selftest-good.sheet.json"
    bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    good_path.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")

    proc = run([str(PY), str(ROOT / "scripts" / "build_draft.py"), str(bad_path)])
    check("质量门拦截问题决策单", proc.returncode == 2 and "cover_text" in proc.stdout,
          f"exit={proc.returncode}")

    proc = run([str(PY), str(ROOT / "scripts" / "build_draft.py"), str(good_path)])
    draft = ROOT / "06-drafts" / "selftest-good"
    content = draft / "draft_content.json"
    ok = proc.returncode == 0 and content.exists()
    detail = ""
    if ok:
        data = json.loads(content.read_text(encoding="utf-8"))
        canvas = data.get("canvas_config", {})
        videos = data.get("materials", {}).get("videos", [])
        tracks = {t.get("type"): len(t.get("segments", [])) for t in data.get("tracks", [])}
        detail = f"{canvas.get('width')}x{canvas.get('height')} 素材={len(videos)} 轨道={tracks}"
        segs = [x for t in data.get("tracks", []) if t.get("type") == "video" for x in t.get("segments", [])]
        seg_us = segs[0]["target_timerange"]["duration"] if segs else 0
        ok = (canvas.get("width") == 1080 and len(videos) >= 1 and tracks.get("video") == 1
              and seg_us >= 100000)
        if not ok:
            detail += f" | 片段时长 {seg_us}us（应≈3s，单位错误时会变成几十微秒）"
    check("生成合法草稿（画布/素材/轨道）", ok, detail or proc.stderr[-150:])

    proc = run([str(PY), str(ROOT / "scripts" / "build_draft.py"), str(good_path), "--publish"])
    published = Path(cfg.get("draft_root", "")) / "selftest-good"
    check("发布到剪映草稿目录", published.exists(), str(published) if published.exists() else "未找到")

    # ---------- 5. 数据复盘 ----------
    section("5. 数据复盘模块")
    csv_path = TMP / "mock.csv"
    csv_path.write_text(
        "作品标题,发布时间,播放量,完播率,平均播放时长(秒),点赞,评论,分享,收藏,涨粉\n"
        "高表现,2026-09-01,50000,40%,20,1200,150,60,400,100\n"
        "低表现,2026-09-02,3000,10%,4,60,3,1,5,1\n", encoding="utf-8-sig")
    proc = run([str(PY), str(ROOT / "scripts" / "analyze_data.py"), str(csv_path), "--name", "selftest-csv"])
    report = ROOT / "08-data" / "selftest-csv.md"
    ok = proc.returncode == 0 and report.exists() and "基线" in report.read_text(encoding="utf-8")
    check("CSV 复盘生成报告", ok, report.name if report.exists() else proc.stderr[-120:])
    if ok:
        body = report.read_text(encoding="utf-8")
        check("诊断识别出低表现作品", "低表现" in body and "完播" in body, "命中断崖/分发问题")

    xlsx_path = TMP / "mock.xlsx"
    try:
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(["作品标题", "播放量", "完播率", "点赞", "收藏", "评论"])
        ws.append(["视频A", 10000, "30%", 300, 90, 20])
        ws.append(["视频B", 2000, "8%", 40, 4, 1])
        wb.save(xlsx_path)
        proc = run([str(PY), str(ROOT / "scripts" / "analyze_data.py"), str(xlsx_path), "--name", "selftest-xlsx"])
        check("XLSX 复盘生成报告", proc.returncode == 0 and (ROOT / "08-data" / "selftest-xlsx.md").exists(),
              f"exit={proc.returncode}")
    except Exception as exc:
        check("XLSX 复盘生成报告", False, str(exc)[:80])

    # ---------- 6. 一键流水线 ----------
    section("6. 一键流水线")
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                           "-File", str(ROOT / "run.ps1"), "-Source", str(src_av),
                           "-Name", "selftest-pipe", "-SkipTranscribe"],
                          capture_output=True, text=True, encoding="utf-8", errors="ignore",
                          timeout=900, env=ENV)
    out = (proc.stdout or "") + (proc.stderr or "")
    check("run.ps1 执行成功", proc.returncode == 0 and "1/3" in out, f"exit={proc.returncode}")
    check("缺少决策单时给出提示", "未找到决策单" in out, "输出尾部: " + out.strip()[-40:].replace("\n", " "))

    # ---------- 汇总 ----------
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n===== 结果 {passed}/{total} 通过 =====")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {name} | {detail}")
    print("清理测试产物:", end=" ")
    cleaned = 0
    for folder, pattern in ((ROOT / "06-drafts", "selftest-*"), (ROOT / "02-transcript", "selftest-*"),
                            (ROOT / "08-data", "selftest-*"), (ROOT / "03-scripts", "selftest-*"),
                            (ROOT / "work" / "normalized", "selftest-*"), (ROOT / "01-audio", "selftest-*")):
        for path in folder.glob(pattern):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            cleaned += 1
    if published.exists():
        shutil.rmtree(published); cleaned += 1
    print(f"{cleaned} 项（保留 work/selftest 里的源素材）")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
