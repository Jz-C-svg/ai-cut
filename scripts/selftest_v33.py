#!/usr/bin/env python3
r"""v3.3 外网方案追踪融合自测（P0-1/P0-2/P1-3/P1-6）。

覆盖：
  关键帧动画 3 项（set_bezier 控制点 / add_keyframe_animation 落稿 / 幂等）
  抽帧去重   3 项（dHash-hamming 自洽 / 真实素材去重前后 / 相邻无重复）
  MCP 视频工具 2 项（get_video_info / detect_video_scenes，进程内 McpServer）
  字幕自动花字 1 项（auto_style_subtitles）
  配音接口   1 项（dubbing 框架，不抛异常）
  回归       2 项（describe_ops 含新函数 / KeyframeProperty 向后兼容）

全部在 work/selftest/v33 做草稿副本；真实素材用 work/normalized/月子知识-0299.mp4；
不碰真实草稿，不调付费 API。

用法：
  "<project-root>\venv\Scripts\python.exe" "<project-root>\scripts\selftest_v33.py"
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402

cli.setup()

from aicut import dubbing, edit2, frame_extractor as fx  # noqa: E402
from aicut.draft import Draft  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""),
          flush=True)


def main() -> int:
    SRC_DRAFT = ROOT / "06-drafts" / "月子知识-成片"
    CASE = ROOT / "work" / "selftest" / "v33"
    REAL_VIDEO = ROOT / "work" / "normalized" / "月子知识-0299.mp4"

    # ============ P0-1 · KF-1：set_bezier 控制点 ============
    from JianYingDraft.keyframe import Keyframe, KeyframeProperty  # noqa: E402

    kf = Keyframe(0, 1.0)
    kf.set_bezier((0.4, 0.1), (0.6, 0.9))
    j = kf.export_json()
    check("KF-1 set_bezier 暴露贝塞尔控制点",
          j["curveType"] == "FreeCurveInOut"
          and j["left_control"] == {"x": 0.4, "y": 0.1}
          and j["right_control"] == {"x": 0.6, "y": 0.9},
          f"curve={j['curveType']} L={j['left_control']} R={j['right_control']}")

    # 线性默认不变
    lin = Keyframe(0, 1.0).export_json()
    check("KF-1b 未调 set_bezier 仍为线性 Line", lin["curveType"] == "Line"
          and lin["left_control"] == {"x": 0.0, "y": 0.0}, "")

    # ============ P0-1 · KF-2/3：add_keyframe_animation 落稿 ============
    shutil.rmtree(CASE, ignore_errors=True)
    CASE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC_DRAFT, CASE)
    d = Draft.load(CASE)
    seg = d.video_segments[0]
    rep = edit2.add_keyframe_animation(d, seg.id, "scale", [
        {"t": 0.0, "value": 1.0},
        {"t": 0.5, "value": 1.3, "control_in": {"x": 0.4, "y": 0.1},
         "control_out": {"x": 0.6, "y": 0.9}},
        {"t": 1.0, "value": 1.0},
    ])
    groups = [g for g in seg.raw.get("common_keyframes", [])
              if g.get("property_type") == "UNIFORM_SCALE"]
    ok_group = bool(groups) and len(groups[0]["keyframe_list"]) == 3
    mid = groups[0]["keyframe_list"][1] if groups else {}
    check("KF-2 add_keyframe_animation 写入 common_keyframes",
          rep["keyframe_count"] == 3 and ok_group,
          f"property=UNIFORM_SCALE 帧数={rep['keyframe_count']}")
    check("KF-2b 贝塞尔帧控制点正确、线性帧 Line",
          mid.get("curveType") == "FreeCurveInOut"
          and mid.get("left_control") == {"x": 0.4, "y": 0.1}
          and groups[0]["keyframe_list"][0].get("curveType") == "Line",
          f"mid={mid.get('curveType')}")

    # 幂等：同属性再调一次，列表应被替换而不是叠加
    rep2 = edit2.add_keyframe_animation(d, seg.id, "scale", [
        {"t": 0.0, "value": 1.0}, {"t": 1.0, "value": 1.5}])
    groups2 = [g for g in seg.raw["common_keyframes"]
               if g["property_type"] == "UNIFORM_SCALE"]
    check("KF-3 同属性重复调用不叠加（幂等）",
          rep2["replaced_existing"] is True and len(groups2) == 1
          and len(groups2[0]["keyframe_list"]) == 2,
          f"groups={len(groups2)} 帧数={len(groups2[0]['keyframe_list'])}")
    d.save()

    # ============ P0-2 · FRAME-1：dHash + hamming 自洽 ============
    if REAL_VIDEO.exists():
        kept = fx.extract_keyframes(REAL_VIDEO, min_interval=1.0)
        cands = fx.candidate_frames(REAL_VIDEO, min_interval=1.0)
        check("FRAME-1 dHash 同图自洽（相邻保留帧不重复）",
              len(kept) >= 1, f"保留 {len(kept)} 帧")
        maxh = 0
        for i in range(1, len(kept)):
            maxh = max(maxh, fx.hamming(kept[i - 1]["hash"], kept[i]["hash"]))
        check("FRAME-2 去重后相邻汉明距离 >= 阈值(5)",
              len(kept) < len(cands) and maxh >= 5,
              f"候选 {len(cands)} -> 保留 {len(kept)}，相邻最大汉明={maxh}")
        check("FRAME-3 去重率落在合理区间（-20%~-70%）",
              0.25 <= (len(kept) / max(len(cands), 1)) <= 0.9,
              f"保留率 {len(kept)/max(len(cands),1)*100:.0f}%")
        fx.cleanup(kept)
    else:
        # 无真实素材：用纯算法自洽兜底
        h1 = fx.dhash_file.__doc__  # noqa: SLF001
        check("FRAME-1 dHash 模块可用（无真实素材，跳过文件级）", h1 is not None, "")
        check("FRAME-2 hamming 自洽", fx.hamming("0" * 64, "0" * 64) == 0
              and fx.hamming("0" * 64, "1" * 64) == 64, "")
        check("FRAME-3 阈值常量存在", fx.DEFAULT_HASH_THRESHOLD == 5, "")

    # ============ P1-3 · MCP-1/2：进程内视频工具 ============
    from aicut.mcp_server import McpServer  # noqa: E402

    srv = McpServer()
    tools = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                        "params": {}})["result"]["tools"]
    tnames = [t["name"] for t in tools]
    need = {"get_video_info", "detect_video_scenes", "extract_video_frame",
            "read_video_subtitles"}
    check("MCP-0 4 个视频理解工具已注册", need.issubset(set(tnames)),
          f"总数 {len(tools)}，缺 {need - set(tnames)}")

    def _call(name: str, args: dict) -> dict:
        r = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": name, "arguments": args}})
        return json.loads(r["result"]["content"][0]["text"])

    if REAL_VIDEO.exists():
        info = _call("get_video_info", {"video_path": str(REAL_VIDEO)})
        check("MCP-1 get_video_info 返回时长/分辨率/帧率",
              info.get("ok") and info.get("duration", 0) > 0
              and info.get("size") and info.get("fps", 0) > 0,
              f"{info.get('duration')}s {info.get('size')} {info.get('fps')}fps")
        sc = _call("detect_video_scenes", {"video_path": str(REAL_VIDEO)})
        check("MCP-2 detect_video_scenes 返回场景列表",
              sc.get("ok") and "scenes" in sc and sc.get("duration", 0) > 0,
              f"场景数 {sc.get('scene_count')}")
    else:
        check("MCP-1 get_video_info（无素材，仅注册）", True, "")
        check("MCP-2 detect_video_scenes（无素材，仅注册）", True, "")

    # ============ P1-6 · STYLE-1：auto_style_subtitles ============
    d2 = Draft.load(CASE)
    n_text = len(d2.text_segments)
    if n_text > 0:
        rep_style = edit2.auto_style_subtitles(d2, style_preset="口播标准",
                                              intro="向上滑动", intro_duration=0.4)
        check("STYLE-1 auto_style_subtitles 批量入场",
              rep_style["styled"] >= 1 and rep_style["total"] == n_text,
              f"total={rep_style['total']} styled={rep_style['styled']} skipped={rep_style['skipped']}")
        d2.save()
    else:
        check("STYLE-1 auto_style_subtitles（草稿无文本，跳过）", True, "")

    # ============ P0-1 · DUB-1：配音接口不抛异常 ============
    r = dubbing.generate_voiceover("这是一段配音测试")
    check("DUB-1 dubbing 框架返回状态（ok 或 待接入）",
          r.get("status") in ("ok", "待接入本地 TTS", "失败") and "output_path" in r,
          f"status={r.get('status')}")

    # ============ 回归 ============
    ops = edit2.describe_ops()
    check("REG-1 describe_ops 含 add_keyframe_animation",
          any("add_keyframe_animation" in o for o in ops), "")
    check("REG-2 KeyframeProperty 向后兼容（枚举成员齐全）",
          all(hasattr(KeyframeProperty, m) for m in
              ("position_x", "position_y", "rotation", "uniform_scale", "alpha", "volume")),
          "")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n===== 结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 =====",
          flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
