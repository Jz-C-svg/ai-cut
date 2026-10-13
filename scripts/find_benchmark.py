#!/usr/bin/env python3
r"""联网找参考视频（v3.7 模板化剪辑引擎 · 模块4）。

输入素材指纹 → 生成搜索关键词 → 候选 URL 下载/分析 → 比对打分 → 输出 Top 参考 + 模板。

用法：
    python scripts/find_benchmark.py --video work/normalized/IMG_5842.mp4 [--max 3]
        [--url "https://www.bilibili.com/video/BVxxxx"]* [--output work/benchmark-result.json]
        [--fallback-material IMG_5850]

降级策略（重要）：
  · 没给 --url 或下载失败时，自动从 work/ 目录挑其他已有 semantic+shotprof+transcript
    的素材做"内部模拟参考"，并在输出里标 degraded: true / degraded_reason。
  · 参考视频只学剪辑结构特征，不下载再分发、不搬运画面；分析后保留指纹 JSON，
    mp4 只作缓存。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import templateclip, videocompare, videofinger  # noqa: E402
from aicut.paths import TRANSCRIPTS, WORK  # noqa: E402

REF_VIDEO_DIR = ROOT / "external" / "refs" / "videos"


# ---------------------------------------------------------------- 关键词生成
def build_search_keywords(fp: dict) -> list[str]:
    """从素材指纹推 3-5 条搜索关键词（给人/agent 拿去搜 B站/抖音/小红书）。"""
    sem = fp.get("semantic", {}) or {}
    kps = sem.get("关键信息点") or []
    topic = kps[0] if kps else (sem.get("叙事摘要") or [""])[0]
    topic = str(topic).strip()
    vtype = fp.get("video_type", "koubo-zhishi")
    type_kw = {
        "koubo-zhishi": "口播 科普 短视频",
        "duibi": "对比 评测 短视频",
        "jiaocheng": "教程 步骤 短视频",
        "qingxu-gushi": "情感故事 短视频",
    }.get(vtype, "短视频 剪辑")
    out = [f"{topic} {type_kw}",
           f"{topic} 抖音",
           f"{sem.get('主角','')} {sem.get('场景','')} {type_kw}".strip()]
    # 去空去重
    seen, uniq = set(), []
    for k in out:
        k = " ".join(k.split())
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq[:5]


# ---------------------------------------------------------------- 候选收集
def _local_candidates(query_stem: str, limit: int = 5) -> list[dict]:
    """从 work/ 挑其他已有完整三份档案的素材做内部模拟参考。"""
    out = []
    for sem_path in sorted(WORK.glob("semantic-*.json")):
        stem = sem_path.stem.replace("semantic-", "", 1)
        if stem == query_stem:
            continue
        sp_path = WORK / f"shotprof-{stem}.json"
        tp_path = TRANSCRIPTS / f"{stem}.json"
        if not (sp_path.exists() and tp_path.exists()):
            continue
        try:
            vf = videofinger.from_profiles(
                semantic=json.loads(sem_path.read_text(encoding="utf-8")),
                shotprof=json.loads(sp_path.read_text(encoding="utf-8")),
                transcript=json.loads(tp_path.read_text(encoding="utf-8")),
                video=str(ROOT / "work" / "normalized" / f"{stem}.mp4"))
            vf["fingerprint"] = f"local-{stem}"
            vf["source_title"] = stem
            out.append(vf)
        except Exception as exc:  # noqa: BLE001
            print(f"[候选] 跳过 {stem}：{type(exc).__name__}")
    out.sort(key=lambda x: x.get("duration", 0), reverse=True)
    return out[:limit]


def _try_download(url: str, out_dir: Path) -> Path | None:
    """尝试用 yt-dlp 下载参考视频；不可用/失败返回 None。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        print("[下载] 未安装 yt-dlp，跳过联网下载")
        return None
    out_tmpl = str(out_dir / "ref_%(id)s.%(ext)s")
    opts = {"outtmpl": out_tmpl, "quiet": True, "no_warnings": True,
            "format": "mp4/best", "noplaylist": True, "merge_output_format": "mp4"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        # 找下载到的文件
        for p in out_dir.glob("ref_*.*"):
            if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".flv"):
                return p
        _ = info
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[下载] {url} 失败：{type(exc).__name__}: {str(exc)[:120]}")
        return None


def _analyze_reference_video(local_path: Path) -> dict | None:
    """对下载到的参考视频跑指纹。转写必须跑；shotprof/semantic 失败时降级为纯转写。"""
    try:
        return videofinger.build(local_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[分析] {local_path.name} 指纹生成失败：{type(exc).__name__}: {exc}")
        return None


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="找参考视频并比对剪辑结构")
    ap.add_argument("--video", required=True, help="素材视频路径")
    ap.add_argument("--max", type=int, default=3, help="最多输出几个候选（默认 3）")
    ap.add_argument("--url", action="append", default=[], help="候选视频 URL，可多次传")
    ap.add_argument("--output", help="结果 JSON 输出路径")
    ap.add_argument("--fallback-material", help="降级模式用的本地模拟参考素材名（如 IMG_5850）")
    ap.add_argument("--no-download", action="store_true", help="不尝试联网下载，直接走降级")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.is_absolute():
        video = ROOT / video
    if not video.exists():
        print(f"找不到素材: {video}", file=sys.stderr)
        return 1

    print(f"[1/5] 生成素材指纹: {video.name}")
    query_fp = videofinger.build(video)
    kws = build_search_keywords(query_fp)
    print(f"      视频类型={query_fp['video_type']}  时长={query_fp['duration']}s")
    print(f"      搜索关键词建议: {kws}")

    candidates_raw: list[dict] = []
    degraded = False
    degraded_reason = ""

    # ---- 联网下载候选 ----
    if args.url and not args.no_download:
        for url in args.url:
            print(f"[2/5] 尝试下载: {url[:80]}")
            local = _try_download(url, REF_VIDEO_DIR)
            if local is None:
                continue
            fp = _analyze_reference_video(local)
            if fp:
                fp["source_url"] = url
                fp["local_path"] = str(local)
                candidates_raw.append(fp)
            # 分析完保留 mp4 作缓存；大文件可手动清理
    else:
        degraded_reason = "未提供 --url 或 --no-download"

    # ---- 降级：本地模拟参考 ----
    if not candidates_raw:
        degraded = True
        if not degraded_reason:
            degraded_reason = "所有联网候选下载/分析失败"
        fallback = args.fallback_material
        if fallback:
            cands = _local_candidates(video.stem, limit=args.max)
            cands = [c for c in cands if Path(c.get("video", "")).stem == fallback] or cands[:1]
        else:
            cands = _local_candidates(video.stem, limit=args.max)
        for c in cands:
            c["source_url"] = ""
            c["local_path"] = c.get("video", "")
            c["simulated"] = True
        candidates_raw = cands
        print(f"[2/5] 降级模式：用 {len(cands)} 个本地已有素材做模拟参考（{degraded_reason}）")

    # ---- 比对打分 ----
    print(f"[3/5] 比对 {len(candidates_raw)} 个候选…")
    ranked = videocompare.rank(query_fp, candidates_raw)

    items = []
    for rank, cand in enumerate(ranked[:args.max], 1):
        tmpl = templateclip.extract_template(cand,
            template_id=f"{query_fp['video_type']}-benchmark-{rank}",
            template_name=f"benchmark-Top{rank}·{cand.get('source_title','ref')}")
        items.append({
            "rank": rank,
            "url": cand.get("source_url", ""),
            "title": cand.get("source_title") or cand.get("video", ""),
            "source": "bilibili/online" if cand.get("source_url") else "local-simulated",
            "local_path": cand.get("local_path", ""),
            "fingerprint": cand,
            "comparison": cand.get("comparison"),
            "template": tmpl,
        })
        print(f"  Top{rank} 分={cand['total_score']:.3f}  {cand.get('source_title','')[:24]}  "
              f"理由={cand['comparison']['match_reason'][:60]}")

    best_id = items[0]["template"]["template_id"] if items else ""
    result = {
        "query_video": str(video),
        "query_fingerprint": query_fp,
        "search_keywords": kws,
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "candidates": items,
        "best_template_id": best_id,
    }

    out_path = Path(args.output) if args.output else WORK / f"benchmark-result-{video.stem}.json"
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[4/5] 结果已写 {out_path}")

    # ---- 清理：保留指纹 JSON，mp4 只留小缓存；大文件提示 ----
    if REF_VIDEO_DIR.exists():
        for p in REF_VIDEO_DIR.glob("ref_*.mp4"):
            try:
                size_mb = p.stat().st_size / 1e6
                print(f"[5/5] 参考视频缓存 {p.name} ({size_mb:.1f}MB) 保留在 external/refs/videos/ 供复跑")
            except OSError:
                pass

    print(f"[完成] Best template: {best_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
