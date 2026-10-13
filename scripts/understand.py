#!/usr/bin/env python3
r"""AI 内容理解：让剪辑引擎"看懂"素材在讲什么、画面是什么。

三层：
  1. 文本理解（LLM）：逐字稿 -> 功能分段、关键词、删除建议、封面与落点候选、语义切点
  2. 视觉理解（本地视觉模型）：抽帧 -> 画面描述、是否适合做封面
  3. 信号识别（analyze_media.py）：镜头切点、停顿、响度（帧级精确）

用法:
    python understand.py --video work/normalized/x.mp4 [--transcript 02-transcript/x.json]
                         [--provider auto|deepseek|ollama] [--frames 4] [--out work/understand.json]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))
from aicut.ollama_endpoint import ollama_url
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
OLLAMA_URL = ollama_url('/api/chat')
SCHEMA_VERSION = 2
OLLAMA_TAGS_URL = ollama_url('/api/tags')
# Prefer the model that completed a real local image inference reliably on this
# 8-GB-VRAM machine. Larger installed models remain selectable explicitly.
DEFAULT_VLM_ORDER = ["gemma4:e2b", "qwen3.8:27b", "qwen3.6:35b"]
FRAME_BATCH_SIZE = 1
LOCAL_LLM = "gemma4:e2b"

PROMPT = """你是短视频剪辑师。下面是一条口播视频的逐字稿（带时间码）。
你的任务不是评论，而是给出可直接执行的剪辑指令。只输出 JSON，不要解释，格式：
{
  "逐句": [
    {"句": "原句", "角色": "钩子/展开/证据/反转/落点", "动作": "保留/删除/压缩",
     "节奏": "快/中/慢", "高亮": ["这句里最长2个需要字幕高亮的词"], "原因": "十个字内"}
  ],
  "钩子句": "哪一句最适合放到开头（必须在逐句里出现过）",
  "封面候选": ["三句封面文案，不用绝对化用语，不超过14字"],
  "落点候选": ["三句行动指令，单一动作"],
  "语义切点": [建议切镜头的秒数，必须落在句子结尾]
}
判断标准：
- 口误、重复、无信息量的句子标 删除；信息密度低但需要保留的标 压缩
- 节奏：结论与钩子用 快，解释与铺垫用 中，情绪或重点用 慢
- 高亮只标数字、结论、动作词，不要标虚词
逐字稿：
"""


def post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                              "[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')"],
                             capture_output=True, text=True, timeout=30)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def llm_json(prompt: str, provider: str) -> tuple[dict | None, str]:
    """让模型输出 JSON；自动模式先本地，并遵守全局付费 API 开关。"""
    order = ["ollama", "deepseek"] if provider == "auto" else [provider]
    for prov in order:
        try:
            if prov == "deepseek":
                from aicut.llm_router import _config
                if not _config().get('ai_router', {}).get('global_paid_api_enabled', True):
                    print('  [deepseek] 全局付费 API 已关闭，跳过')
                    continue
                key = load_key()
                if not key:
                    continue
                r = post_json(DEEPSEEK_URL, {"model": "deepseek-v4-pro",
                                             "messages": [{"role": "user", "content": prompt}],
                                             "response_format": {"type": "json_object"},
                                             "max_tokens": 6000}, {"Authorization": "Bearer " + key})
                msg = r["choices"][0]["message"]
                text = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
                if not text:
                    print("  [deepseek] 返回为空 finish_reason=%s usage=%s" % (r["choices"][0].get("finish_reason"), r.get("usage")))
            else:
                from aicut.llm_router import route_backends
                local = [b for b in route_backends('decision') if b.type == 'ollama']
                model = local[0].model if local else LOCAL_LLM
                r = post_json(OLLAMA_URL, {"model": model, "stream": False, "think": False,
                                           "keep_alive": "10m",
                                           "format": "json",
                                           "messages": [{"role": "user", "content": prompt}],
                                           "options": {"num_predict": 1200}})
                text = r["message"].get("content") or ""
            data = extract_json(text)
            if data:
                return data, prov
            print(f"  [{prov}] 未解析出 JSON（返回 {len(text)} 字）：{text[:80].strip()!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{prov}] 失败: {type(exc).__name__} {str(exc)[:80]}")
    return None, ""


def extract_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def pick_frames(video: Path, analysis: dict | None, count: int) -> list[float]:
    """跨时间均匀覆盖，并以镜头内清晰帧补采；去重后不超预算。"""
    count = max(1, min(int(count), 64))
    info = analysis or {}
    dur = float(info.get("duration") or 0)
    if dur <= 0:
        return []
    scenes = [s for s in info.get("场景", []) if float(s.get("end", 0)) > float(s.get("start", 0))]
    candidates = [dur * (i + 0.5) / count for i in range(count)]
    sharp = [float(s.get("最清晰帧_秒", (float(s["start"])+float(s["end"]))/2)) for s in scenes]
    if len(sharp) > count:
        sharp = [sharp[round(i * (len(sharp)-1) / max(1, count-1))] for i in range(count)]
    candidates.extend(sharp)
    selected: list[float] = []
    min_gap = min(0.75, max(0.2, dur / (count * 2)))
    for t in sorted(candidates):
        t = min(max(0.05, t), max(0.05, dur - 0.05))
        if not selected or all(abs(t - old) >= min_gap for old in selected):
            selected.append(round(t, 2))
    if len(selected) > count:
        if count == 1:
            selected = [selected[len(selected)//2]]
        else:
            selected = [selected[round(i * (len(selected)-1) / (count-1))] for i in range(count)]
    return selected


VISION_PROMPT = """你是视频画面标注器，只依据当前一张图片识别可见事实，不得使用语音主题或常识补全。重点描述画面中心人物/物体、可见动作、主要环境以及构图清晰度；忽略小字和密集文字，不做 OCR。主体/动作/场景各不超过12个字，物品最多4个，依据不超过15个字。看不清写“不确定”。只输出一个 JSON 对象，不要 Markdown，不要解释：
{"id":"F01","主体":"...","动作":"...","物品":["..."],"场景":"...","画面质量":"清晰/可用/模糊/遮挡/不确定","封面候选":false,"依据":"只写可见证据"}
不要输出示例值或省略号，不要推断身份、年龄或意图。"""

ALIGNMENT_PROMPT = """你是素材剪辑的跨模态核对器。视觉描述是已识别的候选事实，语音是转写文本；判断二者在该时间点是否相互支持。不得修改视觉事实，也不要因主题相同就认定镜头相关；需要可见的具体线索。若语音标有“ASR低置信候选”，关系必须写“无法判断”，不得据此提出裁剪。只有连续、清楚的ASR与明确画面证据才可判不匹配；口播人物镜头可能是合理画面。只输出 JSON：{"items":[{"id":"F01","与语音关系":"强匹配/部分匹配/不匹配/无法判断","依据":"简述可见线索；证据不足写无法判断"}]}"""


def local_vision_models() -> list[str]:
    """只从本机安装的视觉模型中选择，避免调用不存在的硬编码标签。"""
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=5) as response:
            tags = json.loads(response.read().decode("utf-8"))
        names = {m.get("name", "") for m in tags.get("models", [])}
        return [name for name in DEFAULT_VLM_ORDER if name in names]
    except Exception:
        return []


def model_supports_vision(model: str) -> bool:
    try:
        info = post_json(ollama_url('/api/show'), {"model": model})
        return "vision" in (info.get("capabilities") or [])
    except Exception:
        return False


def select_vision_model(requested: str | None) -> str:
    from aicut import llm
    if requested:
        if not model_supports_vision(requested):
            raise RuntimeError(f"指定模型 {requested} 不存在或未声明 vision 能力")
        llm.assert_local_vision_healthy(requested)
        return requested
    configured = llm.get_vision_model()
    for name in dict.fromkeys([configured, *local_vision_models()]):
        if model_supports_vision(name):
            try:
                llm.assert_local_vision_healthy(name)
            except RuntimeError:
                continue
            return name
    raise RuntimeError("Ollama 中没有发现已安装且支持 vision 的模型；安装 qwen3.8:27b 或传入 --vision-model 指定模型")


def vision_cloud(img_b64: str, key: str, prompt: str | None = None) -> str:
    payload = {"model": "deepseek-v4-flash-vision-exp", "max_tokens": 2500,
               "messages": [{"role": "user", "content": [
                   {"type": "text", "text": prompt or VISION_PROMPT},
                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + img_b64}}]}]}
    r = post_json("https://api.deepseek.com/v1/chat/completions", payload,
                  {"Authorization": "Bearer " + key}, timeout=300)
    msg = r["choices"][0]["message"]
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()


def vision_local(images: list[str], prompt: str, model: str) -> str:
    from aicut import llm
    llm.assert_local_vision_healthy(model)
    r = post_json(OLLAMA_URL, {"model": model, "stream": False, "think": False, "keep_alive": "30m",
                               "messages": [{"role": "user", "images": images, "content": prompt}],
                               "options": {"num_ctx": 8192, "num_predict": 500, "temperature": 0.1}}, timeout=900)
    return (r["message"].get("content") or "").strip()


def transcript_at(segs: list[dict], t: float) -> str:
    nearby = [s for s in segs if float(s.get("start", 0)) - 1.5 <= t <= float(s.get("end", 0)) + 1.5]
    return " ".join(("ASR低置信候选：" if s.get("低置信度") else "") +
                    str(s.get("text", "")).replace("\n", " ").strip() for s in nearby)[:180]


def describe_frames(video: Path, times: list[float], out_dir: Path, model: str,
                    segs: list[dict]) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    for i, t in enumerate(times):
        frame = out_dir / f"{video.stem}_{str(t).replace('.', '_')}.jpg"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(t),
                        "-i", str(video), "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", str(frame)],
                       capture_output=True, timeout=60)
        if not frame.exists() or frame.stat().st_size == 0:
            samples.append({"id": f"F{i+1:02d}", "t": t, "失败": "抽帧失败", "有效": False})
            continue
        raw = frame.read_bytes()
        samples.append({"id": f"F{i+1:02d}", "t": t, "file": str(frame),
                        "hash": hashlib.sha1(raw).hexdigest(),
                        "image": base64.b64encode(raw).decode(), "speech": transcript_at(segs, t)})
    unique: dict[str, dict] = {}
    for sample in samples:
        if sample.get("image"):
            unique.setdefault(sample["hash"], sample)
    sources = list(unique.values())
    by_id: dict[str, dict] = {}
    by_hash: dict[str, dict] = {}
    for start in range(0, len(sources), FRAME_BATCH_SIZE):
        batch = sources[start:start + FRAME_BATCH_SIZE]
        ids = {x["id"] for x in batch}
        meta = "\n".join(f"{x['id']} 时间 {x['t']:.2f} 秒" for x in batch)
        try:
            answer = vision_local([batch[0]["image"]], VISION_PROMPT + "\n当前帧编号与时间：" + meta, model)
            parsed = extract_json(answer)
            rows = (parsed or {}).get("frames") if isinstance(parsed, dict) else None
            if not rows and isinstance(parsed, dict) and parsed.get("id"):
                rows = [parsed]
            if not rows:
                trace = out_dir / f"{video.stem}_vision-batch-{start+1:02d}.raw.txt"
                trace.write_text(answer, encoding="utf-8")
                print(f"   [视觉格式无效] 模型未返回单帧 JSON；原文记录于 {trace.name}")
            for row in rows or []:
                fid = str(row.get("id", batch[0]["id"]))
                if fid in ids:
                    by_id[fid] = row
                    by_hash[batch[0]["hash"]] = row
            if parsed and parsed.get("frames") and not any(str(x.get("id", "")) in ids for x in parsed["frames"]):
                trace = out_dir / f"{video.stem}_vision-batch-{start+1:02d}.raw.txt"
                trace.write_text(answer, encoding="utf-8")
                print(f"   [视觉帧编号不匹配] 实际编号与 {sorted(ids)} 不一致；原文记录于 {trace.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"   [视觉批次失败] {type(exc).__name__}: {str(exc)[:120]}")
    results = []
    for sample in samples:
        row = by_hash.get(sample.get("hash", ""))
        if not row:
            results.append({"t": sample["t"], "id": sample["id"], "帧文件": sample.get("file", ""),
                            "模型": model, "有效": False, "失败": sample.get("失败", "模型未返回对应帧"),
                            "与语音上下文": sample.get("speech", "")})
        else:
            results.append({**row, "t": sample["t"], "id": sample["id"], "帧文件": sample["file"],
                            "模型": model, "有效": True, "附近语音": sample.get("speech", ""),
                            "与语音关系": "待跨模态核对"})
            print(f"  {sample['t']:>6.2f}s [{model}] {str(row.get('主体', ''))[:30]} / {str(row.get('动作', ''))[:30]}")
    return results


def align_frames_with_speech(frames: list[dict], provider: str) -> str:
    """把视觉证据与转写分开推理；对齐结果作为推断，不回写视觉事实。"""
    candidates = [{k: f.get(k) for k in ("id", "t", "主体", "动作", "物品", "场景", "附近语音")}
                  for f in frames if f.get("有效") and f.get("附近语音")]
    if not candidates:
        return "未运行"
    data, model = llm_json(ALIGNMENT_PROMPT + "\n候选记录：\n" + json.dumps(candidates, ensure_ascii=False), provider)
    if not data:
        return "失败"
    by_id = {str(x.get("id")): x for x in data.get("items", []) if isinstance(x, dict)}
    for frame in frames:
        row = by_id.get(str(frame.get("id")))
        if row:
            frame["与语音关系"] = row.get("与语音关系", "无法判断")
            frame["对齐依据"] = row.get("依据", "")
    return model or "未知"


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 内容理解")
    ap.add_argument("--video", required=True)
    ap.add_argument("--transcript", help="逐字稿 JSON（默认按素材名找）")
    ap.add_argument("--analysis", help="analyze_media 的输出（默认自动生成）")
    ap.add_argument("--provider", default="ollama", choices=["auto", "deepseek", "ollama"],
                    help="文本/对齐模型；默认严格本地 Ollama，只有显式指定 deepseek 才发送逐字稿到云端")
    ap.add_argument("--frames", type=int, default=8, help="视觉分析帧预算（默认8，范围1-64）")
    ap.add_argument("--vision-model", help="指定本地视觉模型；默认选择已安装且适配本机的模型")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--out", default="work/understand.json")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.is_absolute():
        video = ROOT / video
    if not video.exists():
        print(f"找不到视频: {video}", file=sys.stderr)
        return 1

    # 1. 信号层
    analysis_path = Path(args.analysis) if args.analysis else ROOT / "work" / f"analysis-{video.stem}.json"
    if not analysis_path.exists():
        print("[信号识别] 生成中…")
        subprocess.run([str(ROOT / "venv" / "Scripts" / "python.exe"), str(ROOT / "scripts" / "analyze_media.py"),
                        str(video), "--out", str(analysis_path)], cwd=str(ROOT))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.exists() else None

    # 2. 文本理解
    transcript_path = Path(args.transcript) if args.transcript else ROOT / "02-transcript" / f"{video.stem}.json"
    text_result, used = {}, ""
    if transcript_path.exists():
        segs = json.loads(transcript_path.read_text(encoding="utf-8")).get("segments", [])
        lines = "\n".join(f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in segs)
        print(f"[文本理解] {len(segs)} 句，交给模型分析…")
        text_result, used = llm_json(PROMPT + lines, args.provider)
        print(f"  使用模型: {used or '不可用'}")
        if text_result:
            # 兼容：把逐句动作整理成删除列表与关键词
            items = text_result.get("逐句") or []
            if items:
                dels = [{"文本": it.get("句", ""), "原因": it.get("原因", "")}
                        for it in items if it.get("动作") == "删除" and it.get("句")]
                if dels:
                    text_result["删除建议"] = dels
                kws = []
                for it in items:
                    for w in (it.get("高亮") or []):
                        if w and w not in kws:
                            kws.append(w)
                if kws:
                    text_result["关键词"] = kws[:8]
            cuts = text_result.get("语义切点") or []
            nums = []
            for item in cuts:
                if isinstance(item, (int, float)):
                    nums.append(round(float(item), 2))
                else:
                    m = re.findall(r"[0-9]+(?:\.[0-9]+)?", str(item))
                    if m:
                        nums.append(round(float(m[-1]), 2))
            text_result["语义切点_秒"] = sorted({n for n in nums if n > 0.2})
            print(f"  分段 {len(text_result.get('分段', []))} 个 | 关键词 {text_result.get('关键词', [])} "
                  f"| 删除建议 {len(text_result.get('删除建议', []))} 条 | 语义切点 {text_result.get('语义切点', [])}")
    else:
        print(f"[文本理解] 没有逐字稿：{transcript_path}")

    # 3. 视觉理解
    frames = []
    vision_model = ""
    visual_status = "未请求"
    alignment_model = ""
    if not args.no_vision and analysis:
        try:
            vision_model = select_vision_model(args.vision_model)
        except Exception as exc:
            print(f"[视觉理解不可用] {exc}")
            visual_status = "失败"
        times = pick_frames(video, analysis, args.frames)
        print(f"[视觉理解] {len(times)} 个分层抽样帧，使用 {vision_model or '无可用模型'}；批量对齐语音上下文")
        if vision_model:
            frames = describe_frames(video, times, ROOT / "work" / "aivision", vision_model,
                                    segs if transcript_path.exists() else [])
            alignment_model = align_frames_with_speech(frames, "ollama")
            # The on-device E2B model is useful for proposing where to look,
            # but a real-frame audit found material category hallucinations.
            # Keep its raw labels for traceability while withholding them from
            # automatic cross-modal disagreement/cropping decisions.
            if vision_model == "gemma4:e2b":
                for frame in frames:
                    if frame.get("有效"):
                        frame["视觉可信级别"] = "低_待人工复核"
                        frame["与语音关系"] = "无法判断"
                        frame["对齐依据"] = "轻量视觉模型存在类别误判；原始标签仅供人工参考"
            visual_status = "成功" if frames and all(x.get("有效") for x in frames) else \
                            "部分失败" if any(x.get("有效") for x in frames) else "失败"

    st = video.stat()
    st = video.stat()
    best_cover = ""
    for f in frames:
        d = str(f.get("描述", ""))
        if ("适合作为" in d and "不" not in d.split("适合作为")[0][-3:]) or "非常适合" in d:
            best_cover = str(f.get("帧文件", ""))
            break
    if not best_cover:
        for f in frames:
            if f.get("有效") and f.get("封面候选") is True:
                best_cover = str(f.get("帧文件", "")); break
    result = {
        "识别方案版本": SCHEMA_VERSION,
        "video": str(video),
        "素材指纹": {"大小": st.st_size, "修改时间": int(st.st_mtime)},
        "建议封面帧": best_cover,
        "素材指纹": {"大小": st.st_size, "修改时间": int(st.st_mtime)},
        "信号识别": analysis,
        "文本理解": text_result,
        "文本理解模型": used,
        "视觉理解": frames,
        "视觉模型": vision_model,
        "视觉状态": visual_status,
        "跨模态对齐模型": alignment_model,
    }
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入 {out}")
    cover = result.get("建议封面帧")
    if cover and Path(cover).exists():
        d = ROOT / "07-output" / "封面"
        d.mkdir(parents=True, exist_ok=True)
        target = d / (video.stem + "-封面.jpg")
        target.write_bytes(Path(cover).read_bytes())
        print(f"[封面] 建议封面帧已导出: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
