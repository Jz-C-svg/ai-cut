#!/usr/bin/env python3
"""Build paired video + sampled-frame + local-vision-analysis study records.

Only the explicitly listed, rights-checked visual study samples are downloaded.
Every record keeps source attribution, original video hash, per-frame
hashes/timecodes and the local model response; it is not a model-training job.
"""
from __future__ import annotations

import hashlib
import html
import json
import sys
import time
import urllib.request
import traceback
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from aicut import bundled_vision  # noqa: E402

OUT = ROOT / "content-library/web-visuals/paired-samples"
STUDIES = OUT / "studies.jsonl"
SAMPLES = [
    {"id":"pexels-film-noir-conversation","category":"电影镜头与剪辑","title":"Film Noir Detective Conversation",
     "source_url":"https://www.pexels.com/video/a-man-and-woman-in-a-room-with-a-desk-7319502/",
     "download_url":"https://videos.pexels.com/video-files/7319502/7319502-uhd_4096_2160_25fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset.",
     "platform_scope":"film_style_proxy",
     "retrieval_labels":["电影镜头", "电影剪辑", "电影场面调度", "黑色电影", "电影正反打", "镜头语言", "空间关系", "视线连续性"],
     "study_focus":["黑色电影布光与阴影","双人场景空间关系","正反打与视线连续性（若素材确有剪辑）","镜头尺度与叙事信息"]},
    {"id":"pexels-short-video-vlog-creator","category":"短视频平台风格代理","title":"Vertical Vlog Creator",
     "source_url":"https://www.pexels.com/video/woman-recording-a-video-7677003/",
     "download_url":"https://videos.pexels.com/video-files/7677003/7677003-hd_1080_1920_25fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset.",
     "platform_scope":"short_video_style_proxy",
     "retrieval_labels":["短视频平台", "抖音风格代理", "B站风格代理", "竖屏短视频", "口播构图", "短视频创作者"],
     "study_focus":["竖屏人物安全区","口播/自拍视频构图","视觉注意力与背景灯光","创作者场景节奏"]},
    {"id":"pexels-short-video-lifestyle-vlog","category":"短视频平台风格代理","title":"Lifestyle Vlogger Recording Herself",
     "source_url":"https://www.pexels.com/video/a-woman-doing-a-vlog-7677109/",
     "download_url":"https://videos.pexels.com/video-files/7677109/7677109-hd_1080_1920_25fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset.",
     "platform_scope":"short_video_style_proxy","style_type":"竖屏生活/美妆口播",
     "retrieval_labels":["短视频平台风格代理","竖屏短视频","生活vlog","美妆口播","对镜表达","人物安全区"],
     "study_focus":["竖屏自拍景别变化","口播/教程主体构图","灯光与背景层次","人物手势及视线"]},
    {"id":"pexels-short-video-collab-vlog","category":"短视频平台风格代理","title":"Two Women Vlogging Together",
     "source_url":"https://www.pexels.com/video/two-women-vlogging-together-13929665/",
     "download_url":"https://videos.pexels.com/video-files/13929665/13929665-hd_1080_1920_24fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset.",
     "platform_scope":"short_video_style_proxy","style_type":"双人协作/反应式vlog",
     "retrieval_labels":["短视频平台风格代理","双人vlog","多人创作","反应镜头","互动节奏","竖屏短视频"],
     "study_focus":["双人同框与主体切换","互动表情和反应时机","竖屏空间分配","协作内容的节奏节点"]},
    {"id":"pexels-short-video-educational-vlog","category":"短视频平台风格代理","title":"Educational Talking-Head Vlog",
     "source_url":"https://www.pexels.com/video/a-woman-talking-while-filming-herself-4962734/",
     "download_url":"https://videos.pexels.com/video-files/4962734/4962734-hd_1920_1080_25fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset.",
     "platform_scope":"short_video_style_proxy","style_type":"讲解教程/对镜口播",
     "retrieval_labels":["短视频平台风格代理","教育口播","讲解教程","对镜表达","演示节奏","短视频创作者"],
     "study_focus":["对镜讲解的中近景构图","人物、设备和演示物的空间关系","讲解时手势与视线","适合口播的字幕安全区"]},
    {"id":"pexels-short-video-outdoor-diary","category":"短视频平台风格代理","title":"Outdoor Personal Vlog",
     "source_url":"https://www.pexels.com/video/a-woman-doing-vlogging-13929585/",
     "download_url":"https://videos.pexels.com/video-files/13929585/13929585-hd_1080_1920_24fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset.",
     "platform_scope":"short_video_style_proxy","style_type":"户外生活记录/自拍视频",
     "retrieval_labels":["短视频平台风格代理","户外vlog","生活记录","街头自拍","竖屏短视频","移动镜头"],
     "study_focus":["自拍近景与环境交代","人物与街景的画面占比","手持移动构图稳定性","自然光和背景干扰"]},
    {"id":"pexels-film-noir-cafe-scene","category":"电影镜头与剪辑","title":"Actors in a Café Scene",
     "source_url":"https://www.pexels.com/video/a-footage-of-an-actor-on-a-movie-scene-9810706/",
     "download_url":"https://videos.pexels.com/video-files/9810706/9810706-uhd_4096_2160_25fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset.",
     "platform_scope":"film_style_proxy",
     "retrieval_labels":["电影镜头", "电影剪辑", "电影对话场景", "电影场面调度", "镜头覆盖", "电影自然光"],
     "study_focus":["咖啡馆场面调度","人物互动与反应","自然光与背景层次","对话场景覆盖"]},
    {"id":"pexels-contemporary-dance","category":"舞蹈与表演","title":"A Ballet Dancer Performing",
     "source_url":"https://www.pexels.com/video/a-ballet-dancer-performing-7671830/",
     "download_url":"https://videos.pexels.com/video-files/7671830/7671830-hd_1920_1080_25fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset."},
    {"id":"pexels-online-teacher","category":"教育与讲解","title":"Teacher Explaining Her Lesson to Her Student",
     "source_url":"https://www.pexels.com/video/teacher-explaining-her-lesson-to-her-student-6985427/",
     "download_url":"https://videos.pexels.com/video-files/6985427/6985427-hd_1920_1080_25fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset."},
    {"id":"pexels-pet-playing-vertical","category":"宠物与动物","title":"Person Playing with a Dog",
     "source_url":"https://www.pexels.com/video/person-playing-with-a-dog-8498464/",
     "download_url":"https://videos.pexels.com/video-files/8498464/8498464-hd_1080_1920_24fps.mp4",
     "license":"Pexels License; source item marked free. Internal visual study only; not bundled as a production asset."},
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def probe(video: Path) -> dict:
    import subprocess
    p = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name,width,height,avg_frame_rate,nb_read_frames",
                        "-show_entries", "format=duration", "-of", "json", str(video)],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    data = json.loads(p.stdout)
    stream = (data.get("streams") or [{}])[0]
    return {"duration_seconds": float(data["format"]["duration"]),
            "codec": stream.get("codec_name"), "width": stream.get("width"),
            "height": stream.get("height"), "frame_rate": stream.get("avg_frame_rate"),
            "decoded_frames": int(stream.get("nb_read_frames", 0))}


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 100_000:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AI-CUT-visual-study/1.0"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as response, tmp.open("wb") as out:
        if "video/mp4" not in (response.headers.get("Content-Type") or ""):
            raise RuntimeError("source did not return an MP4 video")
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            out.write(block)
    tmp.replace(dest)


def extract(video: Path, info: dict, frames_dir: Path) -> list[dict]:
    import subprocess
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = info["duration_seconds"]
    timestamps = [round(duration * ratio, 3) for ratio in (0.08, 0.34, 0.62, 0.88)]
    rows = []
    for index, seconds in enumerate(timestamps, 1):
        image = frames_dir / f"frame_{index:02d}_{seconds:06.3f}s.jpg"
        if not image.exists():
            subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-y",
                            "-ss", str(seconds), "-i", str(video), "-frames:v", "1", "-q:v", "2",
                            "-vf", "scale=960:-2", str(image)], check=True, capture_output=True)
        if not image.exists() or image.stat().st_size < 1000:
            raise RuntimeError(f"failed to extract representative frame at {seconds}s")
        rows.append({"time_seconds": seconds, "path": image.relative_to(ROOT).as_posix(),
                     "sha256": sha256(image)})
    return rows


def make_sheet(frames: list[dict], sheet_path: Path) -> None:
    images = []
    font = ImageFont.load_default()
    for row in frames:
        im = Image.open(ROOT / row["path"]).convert("RGB")
        im.thumbnail((640, 360))
        panel = Image.new("RGB", (640, 392), "#111827")
        panel.paste(im, ((640 - im.width) // 2, 24 + (360 - im.height) // 2))
        ImageDraw.Draw(panel).text((10, 5), f"t={row['time_seconds']:.3f}s", fill="white", font=font)
        images.append(panel)
    sheet = Image.new("RGB", (1280, 784), "#0b1020")
    for i, im in enumerate(images):
        sheet.paste(im, ((i % 2) * 640, (i // 2) * 392))
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(sheet_path, "JPEG", quality=88)


def main() -> int:
    eligible_samples = [sample for sample in SAMPLES
                        if sample.get("ai_rag_eligible") is True
                        and sample.get("usage_scope") in {"commercially_permitted", "project_owned"}]
    blocked = len(SAMPLES) - len(eligible_samples)
    if blocked:
        print(f"[rights-gate] skipped {blocked} sources without explicit retrieval approval", flush=True)
    if not eligible_samples:
        print("[rights-gate] no approved visual sources; nothing downloaded", flush=True)
        return 0
    if not bundled_vision.is_available():
        raise RuntimeError(json.dumps(bundled_vision.health(), ensure_ascii=False))
    OUT.mkdir(parents=True, exist_ok=True)
    done = {r["id"]: r for r in (json.loads(s) for s in STUDIES.read_text(encoding="utf-8").splitlines())} if STUDIES.exists() else {}
    for sample in eligible_samples:
        if sample["id"] in done and done[sample["id"]].get("status") == "framewise_vision_complete":
            print(f"[skip] {sample['title']} already analyzed", flush=True)
            continue
        folder = OUT / sample["id"]
        video = folder / "source.mp4"
        print(f"[download] {sample['title']}", flush=True)
        download(sample["download_url"], video)
        info = probe(video)
        if not 1 <= info["duration_seconds"] <= 90 or info["decoded_frames"] < 4:
            raise RuntimeError(f"invalid sample video metadata: {info}")
        # A full decode pass is required before using any frames as evidence.
        import subprocess
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(video),
                        "-map", "0:v:0", "-f", "null", "-"], check=True, capture_output=True)
        frames = extract(video, info, folder / "frames")
        sheet = folder / "contact-sheet.jpg"
        make_sheet(frames, sheet)
        record = done.get(sample["id"], {})
        record.update({**sample, "status":"framewise_vision_running", "review_status":"automatic_analysis_not_human_reviewed",
                       "media_type":"video_with_sampled_images_and_vision_text", "video_path":video.relative_to(ROOT).as_posix(),
                       "video_sha256":sha256(video), "video_bytes":video.stat().st_size, "video_metadata":info,
                       "frames":frames, "contact_sheet_path":sheet.relative_to(ROOT).as_posix(),
                       "contact_sheet_sha256":sha256(sheet), "vision_model":bundled_vision.info()["id"],
                       "production_use_allowed":False,
                       "ai_rag_eligible":bool(sample.get("ai_rag_eligible", False)),
                       "usage_scope":sample.get("usage_scope", "historical_terms_review_required"),
                       "analysis_method":"each_timestamped_frame_sent_as_a_separate_image"})
        observations = {o["frame_sha256"]: o for o in record.get("frame_observations", [])}
        total_seconds = float(record.get("analysis_seconds", 0))
        for frame in frames:
            if frame["sha256"] in observations and observations[frame["sha256"]].get("status") == "observed":
                continue
            image_path = ROOT / frame["path"]
            prompt = (
                "你正在对一段视频的单独一帧做视觉分析。只描述这张图像当前能直接看见的事实；"
                "不得假设前后动作、声音、人物身份或画外内容。给出主体、当前姿态/动作、景别与构图、"
                "可见环境/道具、光线与色彩，以及这一帧可支持的剪辑用途。"
                f"研究范围标签：{sample.get('platform_scope','general_visual_study')}。"
                f"本样本观察重点：{json.dumps(sample.get('study_focus',[]),ensure_ascii=False)}。"
                "仅当该画面或视频元数据可直接支持时才评价重点；静态单帧不能证明剪辑顺序或镜头运动。"
                "镜头运动如果单帧无法判断就写‘单帧不可判定’；不要把静态姿态说成连续运动。"
                "返回 JSON：{\"主体\":\"...\",\"可见事实\":\"...\",\"景别与构图\":\"...\","
                "\"动作姿态\":\"...\",\"环境细节\":[\"...\"],\"光线色彩\":\"...\","
                "\"剪辑参考\":\"...\",\"不确定项\":[\"...\"]}"
            )
            print(f"[vision-frame] {sample['title']} t={frame['time_seconds']:.3f}s | {len(observations)+1}/4", flush=True)
            started = time.time()
            last_error = None
            analysis = None
            for attempt in range(1, 4):
                try:
                    analysis = bundled_vision.analyze_frame(
                        image_path, prompt + f"\n请严格输出合法 JSON 对象，不加 Markdown；这是第 {attempt} 次尝试。",
                        max_tokens=700, temperature=0.1, timeout=900)
                    break
                except Exception as exc:
                    last_error = exc
                    print(f"[vision-retry] t={frame['time_seconds']:.3f}s attempt={attempt}/3: {type(exc).__name__}", flush=True)
                    if attempt < 3:
                        time.sleep(1)
            if analysis is None:
                record["status"] = "framewise_vision_incomplete"
                record["failed_frame"] = {"time_seconds":frame["time_seconds"], "frame_sha256":frame["sha256"],
                                           "error":str(last_error), "attempts":3}
                done[sample["id"]] = record
                STUDIES.write_text("".join(json.dumps(v, ensure_ascii=False)+"\n" for v in done.values()), encoding="utf-8")
                raise RuntimeError(f"vision analysis exhausted retries at {frame['time_seconds']}: {last_error}")
            analysis.pop("_image", None)
            if analysis.get("error") or not analysis or not any(str(v).strip() for v in analysis.values() if isinstance(v, str)):
                raise RuntimeError(f"frame vision analysis failed at {frame['time_seconds']}: {analysis}")
            elapsed = round(time.time()-started, 1)
            total_seconds += elapsed
            observations[frame["sha256"]] = {"time_seconds":frame["time_seconds"], "frame_path":frame["path"],
                "frame_sha256":frame["sha256"], "vision_analysis":analysis, "status":"observed",
                "analysis_seconds":elapsed}
            record["frame_observations"] = [observations[f["sha256"]] for f in frames if f["sha256"] in observations]
            record["analysis_seconds"] = total_seconds
            record["analyzed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            record["status"] = "framewise_vision_running"
            done[sample["id"]] = record
            STUDIES.write_text("".join(json.dumps(v, ensure_ascii=False)+"\n" for v in done.values()), encoding="utf-8")
            print(f"[frame-saved] t={frame['time_seconds']:.3f}s | {elapsed}s", flush=True)
        record["status"] = "framewise_vision_complete"
        record["frame_observation_count"] = len(record.get("frame_observations", []))
        if record["frame_observation_count"] != len(frames):
            raise RuntimeError("not all extracted frames have a matching vision observation")
        # Keep contact-sheet analysis only as a secondary cross-frame summary; per-frame records above are authoritative.
        if not record.get("vision_analysis"):
            record["vision_analysis"] = {"限制":"逐帧观察优先；联系图仅辅助，不覆盖逐帧时间码"}
        done[sample["id"]] = record
        done[sample["id"]] = record
        STUDIES.write_text("".join(json.dumps(v, ensure_ascii=False)+"\n" for v in done.values()), encoding="utf-8")
        print(f"[saved] {sample['title']} | {record['analysis_seconds']} sec | {len(frames)} image frames + video + text", flush=True)
    cards = []
    for record in done.values():
        if record.get("status") != "framewise_vision_complete":
            continue
        observations = {o["frame_sha256"]: o for o in record["frame_observations"]}
        frame_cards = []
        for frame in record["frames"]:
            observation = observations[frame["sha256"]]["vision_analysis"]
            frame_src = (ROOT / frame["path"]).relative_to(OUT).as_posix()
            frame_cards.append(f"<figure><img src='{html.escape(frame_src)}' alt='t={frame['time_seconds']:.3f}s'>"
                f"<figcaption>t={frame['time_seconds']:.3f}s<pre>{html.escape(json.dumps(observation,ensure_ascii=False,indent=2))}</pre></figcaption></figure>")
        video_src = (ROOT / record["video_path"]).relative_to(OUT).as_posix()
        cards.append(f"<article><h2>{html.escape(record['title'])} · {html.escape(record['category'])}</h2>"
            f"<p>完整解码 {record['video_metadata']['decoded_frames']} 帧；模型 {html.escape(record['vision_model'])}；"
            f"逐帧独立分析 {record['frame_observation_count']} 张时间码画面；人工复核：待复核。</p>"
            f"<video controls preload='metadata' src='{html.escape(video_src)}'></video><div class='frames'>{''.join(frame_cards)}</div>"
            f"<p><a href='{html.escape(record['source_url'])}' target='_blank'>原始来源页</a> · SHA-256 {record['video_sha256']}</p></article>")
    gallery = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>AI CUT 成套视觉学习样例</title><style>body{font:16px system-ui;background:#f3f4f6;color:#172033;margin:24px}main{max-width:1200px;margin:auto}article{background:white;border-radius:14px;padding:18px;margin:18px 0;box-shadow:0 2px 12px #0001}video{width:min(100%,720px);max-height:480px;background:#111}.frames{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px;margin-top:14px}figure{margin:0;background:#f8fafc;padding:8px;border-radius:10px}img{width:100%;height:200px;object-fit:contain;background:#111}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px system-ui}</style><main><h1>AI CUT 成套视觉学习样例</h1><p>每条样例包含同一原视频、四张准确时间码的真实帧图，以及本机视觉模型对每张帧图分别生成的观察文字。模型自动分析仍待人工复核；这是可追溯的多模态分析记录，不是模型微调训练。</p>"+"\n".join(cards)+"</main></html>"
    (OUT / "index.html").write_text(gallery, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
