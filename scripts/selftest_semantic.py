#!/usr/bin/env python3
r"""v3.4 理解层自测：镜头细分门槛 / VLM 字段强校验 / 视频级语义档案结构。

纯逻辑测试，不依赖真实 Ollama、不调网络、不写真实素材。
用法：
  python scripts\selftest_semantic.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from PIL import Image  # noqa: E402

from aicut import llm, semantic_profile, shotprof  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


# ================================================================ 1. 镜头细分门槛
def test_split_gate() -> None:
    """6-20s 短视频应进入语义细分（被调用抽帧）；<6s 应直接跳过。"""
    tmp_frames: list[Path] = []

    def fake_extract(video, out_dir, fps=2.0, width=320):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        got = []
        for i in range(20):  # 造 20 张真实小图，供帧差曲线读取
            p = out_dir / f"{i:05d}.jpg"
            Image.new("L", (32, 32), 128).save(p, "JPEG")
            got.append(p)
        tmp_frames.extend(got)
        return got

    stream = {"duration": 14.78}
    one_shot = [{"i": 0, "t0": 0.0, "t1": 14.78, "len": 14.78}]

    with mock.patch.object(shotprof.vision, "extract_frames", side_effect=fake_extract) as m:
        out = shotprof._semantic_split(Path("x.mp4"), stream, one_shot)
        check("细分门槛：14.78s/1镜头 进入语义细分", m.called, f"调用 {m.call_count} 次")
        check("细分门槛：返回结果非空", isinstance(out, list) and len(out) >= 1)

    tmp_frames.clear()
    with mock.patch.object(shotprof.vision, "extract_frames", side_effect=fake_extract) as m:
        shotprof._semantic_split(Path("x.mp4"), {"duration": 3.0}, one_shot)
        check("细分门槛：3.0s 视频直接跳过不抽帧", not m.called)


# ================================================================ 2. VLM 字段强校验
def test_field_validation() -> None:
    frame = Path("dummy.jpg")

    # 第一轮只回 4 个字段，第二轮补齐缺的 4 个
    def fake_vision(img, prompt, *, provider="ollama"):
        if "缺少" in prompt:  # 补全重问
            return {"emotion_tag": "calm", "shot_motion": "locked",
                    "confidence": 0.7, "可辨认度": 0.8, "_provider": "ollama"}
        return {"content": "戴口罩的医护人员面对镜头", "shot_size": "medium",
                "shot_angle": "eye_level", "美学分": 0.6, "_provider": "ollama"}

    with mock.patch.object(llm, "vision_json", side_effect=fake_vision) as m:
        understanding, source, slots = shotprof._understand_merged(frame, use_cloud=False)
        check("字段校验：缺字段触发一次补全重问", m.call_count == 2, f"调用 {m.call_count} 次")
        check("字段校验：_retry_count=1", slots.get("_retry_count") == 1)
        check("字段校验：8字段全部补齐后完整率=1.0",
              abs(slots.get("_field_completeness", 0) - 1.0) < 1e-6,
              str(slots.get("_field_completeness")))
        check("字段校验：补到的 emotion_tag 生效", slots.get("emotion_tag") == "calm")

    # 两轮都缺字段 -> 兜底默认值
    partial = {"content": "主体", "shot_size": "medium", "_provider": "ollama"}

    def always_partial(img, prompt, *, provider="ollama"):
        return dict(partial)

    with mock.patch.object(llm, "vision_json", side_effect=always_partial) as m:
        understanding, source, slots = shotprof._understand_merged(frame, use_cloud=False)
        check("字段校验：补全失败仍重问一次", m.call_count == 2)
        check("字段校验：未拿到的字段走枚举兜底",
              slots.get("shot_angle") == "eye_level" and slots.get("shot_motion") == "locked"
              and slots.get("emotion_tag") == "neutral" and slots.get("confidence") == 0.3
              and understanding.get("可辨认度") == 0.5 and understanding.get("美学分") == 0.5,
              str({k: slots.get(k) for k in ("shot_angle", "shot_motion", "emotion_tag")}))
        check("字段校验：只拿到2字段，完整率=0.25",
              abs(slots.get("_field_completeness", 0) - 0.25) < 1e-6,
              str(slots.get("_field_completeness")))


# ================================================================ 3. 语义档案结构（纯函数）
def test_semantic_assemble() -> None:
    raw = {
        "主角": "戴口罩的医护人员",
        "场景": "室内医疗环境",
        "叙事摘要": ["介绍黄疸", "讲高峰期", "讲消退时间"],
        "关键信息点": ["生理性黄疸2-3天出现", "两周内消退"],
        "情绪基调": "专业/平静",
        "适合结构": "koubo-zhishi",
        "高光候选": [{"t": 5.6, "理由": "讲到高峰期"}],
    }
    segments = [
        {"start": 0.0, "end": 1.8, "text": "这就是黄疸"},
        {"start": 1.8, "end": 5.6, "text": "正常的生理性黄疸两到三天出现"},
        {"start": 5.6, "end": 9.2, "text": "五到七天高峰期"},
    ]
    shots = [
        {"t0": 0.0, "t1": 5.0, "content": "医护人员面对镜头"},
        {"t0": 5.0, "t1": 14.78, "content": "举起宝宝示意"},
    ]
    res = semantic_profile._assemble(
        video=Path("x.mp4"), fp="abc123", duration=14.78, raw=raw,
        segments=segments, shots=shots, frame_times=[2.0, 5.6, 9.2],
        source="ollama", transcript_file="02-transcript/x.json")

    for k in ("主角", "场景", "叙事摘要", "关键信息点", "情绪基调", "适合结构",
              "高光候选", "叙事时间轴", "代表帧时间点", "字段完整率", "来源"):
        check(f"语义档案：含字段 {k}", k in res)
    check("语义档案：字段完整率=1.0", abs(res["字段完整率"] - 1.0) < 1e-6,
          str(res["字段完整率"]))
    check("语义档案：高光候选保留VLM给的t",
          res["高光候选"][0]["t"] == 5.6, str(res["高光候选"]))

    # 时间轴对齐：mid=3.7 -> 落在 0-5s 镜头
    tl = res["叙事时间轴"]
    check("叙事时间轴：三段台词都在", len(tl) == 3)
    check("叙事时间轴：中段对齐到对应画面",
          tl[1]["台词"].startswith("正常") and tl[1]["画面"] == "医护人员面对镜头",
          str(tl[1]))
    check("叙事时间轴：第三段切到第二个镜头画面",
          tl[2]["画面"] == "举起宝宝示意", str(tl[2]))

    # 非法适合结构 -> 兜底 koubo-zhishi；高光缺失 -> 启发式
    raw2 = dict(raw); raw2["适合结构"] = "随便填"; raw2["高光候选"] = []
    res2 = semantic_profile._assemble(
        video=Path("x.mp4"), fp="a", duration=14.78, raw=raw2,
        segments=segments, shots=shots, frame_times=[], source="ollama", transcript_file="")
    check("语义档案：非法适合结构兜底为 koubo-zhishi",
          res2["适合结构"] == "koubo-zhishi")
    check("语义档案：VLM未给高光时启发式兜底",
          len(res2["高光候选"]) >= 1 and 0 <= res2["高光候选"][0]["t"] <= 14.78,
          str(res2["高光候选"]))

    # 代表帧时间点均匀
    times = semantic_profile._rep_times(14.78, 4)
    check("代表帧：均匀取 4 个点", len(times) == 4 and times[0] > 0 and times[-1] < 14.78,
          str(times))


def main() -> int:
    print("== v3.4 理解层纯逻辑自测 ==")
    test_split_gate()
    test_field_validation()
    test_semantic_assemble()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
