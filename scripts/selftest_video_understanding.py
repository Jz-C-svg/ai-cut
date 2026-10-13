#!/usr/bin/env python3
"""统一视频理解档案的无网络回归测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut.video_understanding import build  # noqa: E402


def main() -> int:
    doc = build("fixture.mp4", signal={
        "duration": 10, "镜头切点_秒": [4], "停顿区间_秒": [[0, 0.5]],
        "停顿总时长": 0.5, "响度": {"整合响度_LUFS": -20},
        "场景": [{"start": 0, "end": 10}],
    }, visual={"duration": 10, "cuts": [4], "shots": [
        {"i": 0, "t0": 0, "t1": 4, "grade": "清晰", "face_score": 0.8},
        {"i": 1, "t0": 4, "t1": 10, "grade": "一般", "face_score": 0.4},
    ]}, transcript={"segments": [
        {"start": 0.5, "end": 2.0, "text": "先看一个关键点"},
        {"start": 4.2, "end": 9.0, "text": "再按步骤完成检查"},
    ]})
    assert doc["schema_version"] == "video-understanding-1.0"
    assert len(doc["evidence"]["shots"]) == 2
    assert len(doc["evidence"]["transcript"]) == 2
    assert doc["inference"]["content_type"] == "口播知识型"
    assert doc["inference"]["creative_readiness"] == "ready_for_planning"
    print(json.dumps({"ok": True, "recommendations": len(doc["recommendations"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
