"""实时打点桥接：读 ai-auto-editor 的 program watch 打点 JSON -> 草稿操作计划。

链路位置（"实时看 -> 决策 -> 改草稿"的中间段）：
  ai-auto-editor program watch 实时识别镜头 -> data/program_edits/打点_*.json
    -> load_marks()            解析成结构化镜头清单
    -> marks_to_operations()   翻译成 split / delete_low_score 操作计划
    -> (skills_realtime) apply_realtime_marks 技能落到草稿上

打点 JSON 里的 shot.start/end 就是剪映时间线时间（秒）——ai-auto-editor 看的就是
剪映播放器，播放头位置 == 时间线位置，所以**不需要任何时间换算**，直接喂给
edit2.split_at(draft, t) 即可。
"""
from __future__ import annotations

import json
from pathlib import Path

# 与 ai-auto-editor config.yaml 的默认阈值对齐（program.min_score_delete）
DEFAULT_MIN_SCORE = 0.35


def load_marks(path: str | Path) -> dict:
    """解析打点 JSON，返回结构化 dict。

    返回结构：
      program / mode / created_at / summary / delete_advice / marks
      shots            镜头列表 [{start, end, duration, score, tags, faces}, ...]
      cut_points       切点秒数列表 = 每个镜头的 end（去掉最后一个），升序去重
      min_score        本次解析携带的低分阈值（由调用方决定，这里只透传默认值）
    """
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8-sig"))
    shots = [
        {
            "start": float(s.get("start", 0.0)),
            "end": float(s.get("end", 0.0)),
            "duration": float(s.get("duration", 0.0)),
            "score": float(s.get("score", 0.0)),
            "tags": list(s.get("tags") or []),
            "faces": int(s.get("faces", 0)),
        }
        for s in (raw.get("shots") or [])
    ]
    shots.sort(key=lambda s: s["start"])
    # 切点 = 每个镜头的结束（最后一个镜头的 end 就是素材尾巴，不需要切）
    cuts = sorted({round(s["end"], 3) for s in shots[:-1]} if len(shots) > 1 else set())
    return {
        "program": raw.get("program", ""),
        "mode": raw.get("mode", ""),
        "created_at": raw.get("created_at", ""),
        "summary": raw.get("summary") or {},
        "shots": shots,
        "cut_points": cuts,
        "delete_advice": raw.get("delete_advice") or [],
        "marks": raw.get("marks") or [],
        "source": str(p),
    }


def latest_marks(directory: str | Path, *, pattern: str = "*.json") -> Path | None:
    """扫描打点目录，返回最新的打点文件路径（按修改时间，文件名时间戳兜底）。"""
    d = Path(directory)
    if not d.is_dir():
        return None
    files = [f for f in d.glob(pattern) if f.is_file()]
    if not files:
        return None

    def key(f: Path) -> tuple[float, str]:
        try:
            return (f.stat().st_mtime, f.name)
        except OSError:
            return (0.0, f.name)

    return max(files, key=key)


def low_score_shots(marks: dict, min_score: float = DEFAULT_MIN_SCORE) -> list[dict]:
    """score 低于阈值的镜头（质量太差，建议整段删掉）。"""
    return [dict(s, why="low_score") for s in marks.get("shots", [])
            if s["score"] < min_score]


def marks_to_operations(marks: dict, draft=None, *,
                       min_score: float = DEFAULT_MIN_SCORE,
                       delete_low: bool = False) -> list[dict]:
    """把打点翻译成可直接执行的草稿操作计划（list[op]）。

    op 形态：
      {"op": "split", "t": 3.28}                         # 在 t 秒切开
      {"op": "delete_low_score", "range": [0, 3.28],
       "score": 0.094}                                   # 整段删掉低分镜头

    顺序约定（执行安全）：先按时间升序排好所有 split，再从后往前排 delete——
    这样前面的坐标不会被后面的 ripple_delete 挤歪。split_at 本身对顺序不敏感，
    但删除必须从右往左。

    draft 可选：传了就把超出草稿时长的切点标记出来（split_at 会自动忽略，这里只是
    让计划更可读）；不传也不影响正确性。
    """
    ops: list[dict] = []
    dur = None
    if draft is not None:
        dur = getattr(draft, "declared_duration", None)

    for t in marks.get("cut_points", []):
        op = {"op": "split", "t": round(float(t), 3)}
        if dur is not None and t >= dur - 1e-3:
            op["note"] = f"{t:.2f}s 已超出草稿时长 {dur:.2f}s，split_at 会自动忽略"
        ops.append(op)

    if delete_low:
        lows = sorted(low_score_shots(marks, min_score), key=lambda s: s["start"], reverse=True)
        for s in lows:
            ops.append({
                "op": "delete_low_score",
                "range": [round(s["start"], 3), round(s["end"], 3)],
                "score": round(s["score"], 3),
                "tags": s["tags"],
            })
    return ops


def summarize_ops(ops: list[dict]) -> str:
    """给人看的一行计划摘要。"""
    splits = [o for o in ops if o["op"] == "split"]
    dels = [o for o in ops if o["op"] == "delete_low_score"]
    parts = [f"切 {len(splits)} 处 @ " + "/".join(f"{o['t']:.2f}" for o in splits)]
    if dels:
        removed = round(sum(o["range"][1] - o["range"][0] for o in dels), 2)
        parts.append(f"删 {len(dels)} 段低分镜头共 {removed:.2f}s")
    return "，".join(parts)
