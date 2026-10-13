"""Build a review-first dataset for when/how to add kinetic text and effects.

The source media, OCR observations and frames are read-only. This script does
not promote weak candidates to labels, copy media, train weights or touch RAG.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


VERSION = "effect-decision-review-v1"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _sample_evenly(rows: list[dict], limit: int) -> list[dict]:
    rows = sorted(rows, key=lambda row: (float(row["time"]), str(row.get("id", ""))))
    if len(rows) <= limit:
        return rows
    if limit <= 1:
        return [rows[len(rows) // 2]]
    indexes = sorted({round(i * (len(rows) - 1) / (limit - 1)) for i in range(limit)})
    return [rows[i] for i in indexes]


def _context(shots: list[dict], timestamp: float) -> dict:
    shot = next((s for s in shots if float(s["start"]) <= timestamp < float(s["end"])), None)
    segments = (shot or {}).get("transcript_segments") or []
    text = "".join(str(s.get("text", "")) for s in segments if s.get("start", 0) <= timestamp + 4 and s.get("end", 0) >= timestamp - 4)
    window_start = max(0.0, timestamp - 3.0)
    window_end = timestamp + 3.0
    window_segments = [s for s in segments if float(s.get("start", 0)) < window_end and float(s.get("end", 0)) > window_start]
    return {
        "shot_start": float(shot["start"]) if shot else None,
        "shot_end": float(shot["end"]) if shot else None,
        "shot_duration": float(shot["duration"]) if shot else None,
        "transcript_context": " ".join(str(s.get("text", "")).strip() for s in window_segments).strip(),
        "transcript_segment_count": len(window_segments),
        "asr_confidence_mean": (sum(float(s.get("avg_logprob", 0)) for s in window_segments) / len(window_segments)) if window_segments else None,
    }


def _frame_features(frames: list[dict], timestamp: float) -> dict:
    frame = min(frames, key=lambda f: abs(float(f["time"]) - timestamp)) if frames else {}
    return {
        "frame_time": frame.get("time"), "frame": frame.get("file"),
        "frame_sha256": frame.get("sha256"), "frame_time_delta": abs(float(frame.get("time", timestamp)) - timestamp) if frame else None,
        "mean_luma": frame.get("mean_luma"), "laplacian_variance": frame.get("laplacian_variance"),
        "dark_pixel_ratio": frame.get("dark_pixel_ratio"), "bright_pixel_ratio": frame.get("bright_pixel_ratio"),
        "frame_review_status": frame.get("review_status", "unknown"),
    }


def build(source_root: Path, out: Path, *, per_class: int = 3) -> dict:
    source_root = source_root.resolve()
    out = out.resolve()
    if source_root == out or source_root not in out.parents:
        raise ValueError("output must be a new subdirectory under the source dataset")
    visual = source_root / "production-visual"
    index = read_json(visual / "index.json")
    manifest = read_json(source_root / "manifest.json")
    rights = {row.get("sha256"): row.get("rights_status", "unknown") for row in manifest}
    events_path = visual / "kinetic-text" / "kinetic-text-candidates.jsonl"
    events = read_jsonl(events_path)
    by_hash: dict[str, list[dict]] = {}
    for event in events:
        by_hash.setdefault(event.get("source_sha256", ""), []).append(event)

    examples = []
    for row in index:
        production_path = Path(row["production"])
        if not production_path.exists():
            continue
        prod = read_json(production_path)
        sha = row.get("sha256") or prod.get("source_sha256")
        video_events = by_hash.get(sha, [])
        positive = []
        event_times = []
        for event in video_events:
            for obs in event.get("observations", []):
                timestamp = float(obs["time"])
                event_times.append(timestamp)
                positive.append({"time": timestamp, "id": event.get("id"), "event": event,
                                 "observation": obs, "candidate_basis": "large_text_ocr_candidate_unconfirmed"})
        negative = []
        for frame in prod.get("frames", []):
            timestamp = float(frame["time"])
            if any(abs(timestamp - t) <= 0.15 for t in event_times):
                continue
            negative.append({"time": timestamp, "id": frame.get("sha256", ""), "frame_row": frame,
                             "candidate_basis": "no_large_text_candidate_detected_at_sampled_frame"})
        chosen = _sample_evenly(positive, per_class) + _sample_evenly(negative, per_class)
        shots = prod.get("candidate_shots", [])
        for item in chosen:
            timestamp = float(item["time"])
            if "event" in item:
                event, obs = item["event"], item["observation"]
                text = obs.get("text", "")
                bbox = obs.get("bbox_normalized")
                confidence = obs.get("confidence")
                zone = obs.get("vertical_zone")
                event_id = item["id"]
                evidence_frame, evidence_sha = obs.get("frame"), obs.get("frame_sha256")
            else:
                text, bbox, confidence, zone = "", None, None, None
                event_id = None
                evidence_frame = item["frame_row"].get("file")
                evidence_sha = item["frame_row"].get("sha256")
            context = _context(shots, timestamp)
            frame = _frame_features(prod.get("frames", []), timestamp)
            key = f"{sha}|{timestamp:.3f}|{event_id or 'control'}"
            examples.append({
                "example_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:24],
                "source_sha256": sha, "source_video": prod.get("source"),
                "rights_status": rights.get(sha, "unknown"), "time": round(timestamp, 3),
                "candidate_basis": item["candidate_basis"], "observed_screen_text": text,
                "observed_text_confidence": confidence, "observed_text_bbox_normalized": bbox,
                "observed_text_zone": zone, "ocr_event_id": event_id,
                "evidence_frame": evidence_frame, "evidence_frame_sha256": evidence_sha,
                "frame_features": frame, **context,
                "decision_label": {
                    "status": "unreviewed", "should_add_text": None,
                    "should_add_effect": None, "best_timing_start": None,
                    "best_timing_end": None, "text_role": None,
                    "effect_type": None, "style_id": None,
                    "animation": None, "intensity": None,
                    "placement": None, "reason": None, "reviewer": None,
                },
                "split": "pending_source_group_split",
            })

    out.mkdir(parents=True, exist_ok=True)
    target = out / "decision-review-candidates.jsonl"
    existing_rows = read_jsonl(target)
    existing_by_id = {row.get("example_id"): row for row in existing_rows if row.get("example_id")}
    generated_ids = {row["example_id"] for row in examples}
    preserved_ids = set()
    for row in examples:
        old = existing_by_id.get(row["example_id"])
        if old:
            old_label = old.get("decision_label") or {}
            if old_label.get("status") == "reviewed" or any(
                value is not None for key, value in old_label.items() if key != "status"
            ):
                row["decision_label"] = old_label
                preserved_ids.add(row["example_id"])
    # Keep reviewed/partially annotated rows even if the sampling configuration changes.
    for old in existing_rows:
        label = old.get("decision_label") or {}
        annotated = label.get("status") == "reviewed" or any(
            value is not None for key, value in label.items() if key != "status"
        )
        if annotated and old.get("example_id") not in generated_ids:
            examples.append(old)
            preserved_ids.add(old["example_id"])
    target.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in examples), encoding="utf-8")
    summary = {
        "version": VERSION, "source_videos": len(index), "ocr_candidate_events": len(events),
        "review_examples": len(examples),
        "positive_ocr_candidates_unconfirmed": sum(x["candidate_basis"].startswith("large_text") for x in examples),
        "negative_controls_unconfirmed": sum(x["candidate_basis"].startswith("no_large_text") for x in examples),
        "human_gold_labels": sum((x.get("decision_label") or {}).get("status") == "reviewed" for x in examples),
        "preserved_annotated_rows": len(preserved_ids),
        "reviewed_style_references_existing": len(read_jsonl(visual / "kinetic-text" / "reviewed-style-observations.jsonl")),
        "model_training_performed": False, "source_media_modified": False,
        "rights_verified_for_redistribution": False,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    schema = {
        "version": VERSION,
        "label_policy": "Only human-reviewed labels with status=reviewed are eligible for training/evaluation. OCR candidates and empty samples are weak evidence, not add/no-add ground truth.",
        "split_policy": "Assign train/validation/test by source_sha256 or shoot/event group, never by frame, to prevent same-video leakage.",
        "decision_label_fields": list(examples[0]["decision_label"].keys()) if examples else [],
        "timing_note": "Sparse sampled frames do not determine exact effect onset/offset. Reviewers must watch a time window and enter verified intervals.",
    }
    (out / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = f"""# 花字与特效时机决策数据集（待人工标注）

版本：{VERSION}

本轮从现有账号导出视觉/OCR档案构建了 {len(examples)} 条**待复核候选**：其中 OCR 大字候选 {summary['positive_ocr_candidates_unconfirmed']} 条、未检测到大字的对照画面 {summary['negative_controls_unconfirmed']} 条。每条尽量带有源视频 SHA-256、时间戳、证据帧、字幕上下文、清晰度/亮度特征和版权状态。

这些不是“应该加/不应该加”的标签。大字可能是字幕、门牌或屏幕实物文字；没有 OCR 结果也不能证明没有花字。现有 3 条目视样式观察只确认了抽样帧上的静态外观，不含精确出现区间或决策标签，不能单独训练时机模型。

## 标注字段

在 `decision-review-candidates.jsonl` 对应的 `decision_label` 中填写：`should_add_text`、`should_add_effect`、精确起止时间、文本角色、效果/模板/动画/强度/位置、理由和复核人；确认完成后将 `status` 改为 `reviewed`。必须查看证据帧前后的视频片段，不能仅凭 OCR 采样跨度填写准确时间。

## 使用边界

- 当前人工金标准数：0；真实训练未执行。
- 只有人工审核、授权范围明确的标签才进入训练/评测；当前数据按原视频哈希分组后再切分，避免同一视频帧泄漏到不同集合。
- 原始视频、OCR 档案未被修改或复制；本目录仅存路径与特征引用。
- 本地账号素材外部分发/训练授权未核验，不得打包、上传或用于外部训练。
"""
    (out / "README.md").write_text(readme, encoding="utf-8")
    (out / "review-queue.json").write_text(json.dumps({"version": VERSION,
        "example_ids": [x["example_id"] for x in examples if (x.get("decision_label") or {}).get("status") != "reviewed"],
        "status": "unreviewed"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out")
    ap.add_argument("--per-class", type=int, default=3)
    args = ap.parse_args()
    source = Path(args.dataset)
    target = Path(args.out) if args.out else source / "production-visual" / "kinetic-text" / "effect-decision-v1"
    if args.per_class < 1 or args.per_class > 20:
        ap.error("--per-class must be 1..20")
    result = build(source, target, per_class=args.per_class)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
