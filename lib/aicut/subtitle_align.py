r"""字幕时间轴单调 DP 对齐 + 严格校验（v3.1）。

纯算法模块，不调 LLM、不依赖 numpy（标准库即可）。

设计：
  - align_subtitles_dp  把字幕起止时间对齐到源事件（镜头切点/词边界），
    保持单调不减，每条字幕恰好映射一个源事件，偏移 ≤ max_shift。
  - validate_subtitle_timeline  严格校验：单调 / 无重复来源 / 无负时长 / 同层无重叠。

DP 状态：
  dp[i][j] = 前 i 条字幕对齐到前 j 个源事件（第 i 条对齐到第 j 个事件）的最小总偏移
  转移：dp[i][j] = cost(i,j) + min_{k<j} dp[i-1][k]，cost ≤ max_shift 才允许
  回溯：prev[i][j] 记录最优 k
"""
from __future__ import annotations

from typing import Any

INF = float("inf")


# ---------- 单调 DP 对齐 ----------


def align_subtitles_dp(
    subtitle_times: list[tuple[float, float]],
    source_events: list[tuple[float, float]],
    *,
    max_shift: float = 0.3,
) -> list[tuple[float, float]]:
    """把字幕时间戳对齐到源事件，保持单调不减。

    Args:
        subtitle_times: 字幕列表，每项 (start, end) 秒。
        source_events:  源事件列表，每项 (start, end) 秒；用 start 作为对齐锚点。
        max_shift:      单条字幕允许的最大偏移（秒）。

    Returns:
        对齐后的 (start, end) 列表，顺序与输入一致；duration 保持不变。
        若无法在 max_shift 内找到合法对齐，原样返回。
    """
    n = len(subtitle_times)
    m = len(source_events)
    if n == 0 or m == 0:
        return list(subtitle_times)

    # 源事件按 start 排序（保证单调）
    events_sorted = sorted(enumerate(source_events), key=lambda x: x[1][0])
    event_starts = [e[1][0] for e in events_sorted]
    # 记录原始事件索引，方便回溯
    orig_indices = [e[0] for e in events_sorted]

    # dp[i][j]: 前 i 条字幕，第 i 条对齐到排序后第 j 个事件的最小总代价
    dp: list[list[float]] = [[INF] * m for _ in range(n + 1)]
    prev: list[list[int]] = [[-1] * m for _ in range(n + 1)]

    # base: 0 条字幕
    for j in range(m):
        dp[0][j] = 0.0

    for i in range(1, n + 1):
        sub_start = subtitle_times[i - 1][0]
        for j in range(m):
            shift = abs(sub_start - event_starts[j])
            if shift > max_shift:
                continue
            # 找 k < j 的最小 dp[i-1][k]
            best_k = -1
            best_val = INF
            for k in range(j):
                if dp[i - 1][k] < best_val:
                    best_val = dp[i - 1][k]
                    best_k = k
            if best_k >= 0:
                dp[i][j] = best_val + shift
                prev[i][j] = best_k

    # 找最优终点
    best_j = min(range(m), key=lambda j: dp[n][j])
    if dp[n][best_j] == INF:
        # 无法在 max_shift 内合法对齐
        return list(subtitle_times)

    # 回溯
    alignment = [0] * n  # alignment[i] = 排序后事件索引
    j = best_j
    for i in range(n, 0, -1):
        alignment[i - 1] = j
        j = prev[i][j]

    # 构建输出：start 对齐到事件 start，duration 不变
    result: list[tuple[float, float]] = []
    for i in range(n):
        dur = subtitle_times[i][1] - subtitle_times[i][0]
        aligned_start = event_starts[alignment[i]]
        result.append((round(aligned_start, 6), round(aligned_start + dur, 6)))
    return result


# ---------- 严格校验 ----------


def validate_subtitle_timeline(subtitles: list[dict]) -> dict:
    r"""严格校验字幕时间轴。

    Args:
        subtitles: 每项 dict，支持键：
          - "t0"/"start": 起点秒
          - "t1"/"end":   终点秒
          - "layer":     层号（可选，同层重叠才报错）
          - "source":     来源事件索引（可选，校验无重复/遗漏）
          - "id"/"text":  标识（用于报错信息）

    Returns:
        {"valid": bool, "violations": [str, ...]}
    """
    violations: list[str] = []

    def _t0(s: dict) -> float:
        return float(s.get("t0", s.get("start", 0.0)))

    def _t1(s: dict) -> float:
        return float(s.get("t1", s.get("end", 0.0)))

    if not subtitles:
        return {"valid": True, "violations": []}

    # ① 时间单调不减（按原始顺序检查，而非排序后）
    for i in range(1, len(subtitles)):
        if _t0(subtitles[i]) < _t0(subtitles[i - 1]) - 1e-6:
            label = subtitles[i].get("text") or subtitles[i].get("id") or f"#{i}"
            violations.append(
                f"时间倒序：「{label}」起点 {_t0(subtitles[i]):.3f}s "
                f"早于前一条 {_t0(subtitles[i-1]):.3f}s")

    # ③ 无负时长
    for i, s in enumerate(subtitles):
        dur = _t1(s) - _t0(s)
        if dur <= 1e-6:
            label = s.get("text") or s.get("id") or f"#{i}"
            violations.append(f"负时长/零时长：「{label}」 dur={dur:.3f}s")

    # ② 每条来源恰好一次（无重复/遗漏）
    source_list = [s.get("source") for s in subtitles if "source" in s]
    if source_list:
        seen: dict[Any, int] = {}
        for idx, src in enumerate(source_list):
            seen[src] = seen.get(src, 0) + 1
        for src, cnt in seen.items():
            if cnt > 1:
                violations.append(f"来源 {src!r} 被引用 {cnt} 次（应恰好一次）")
        # 遗漏检查：若 source 是 0..N-1 的连续索引，检查是否缺号
        int_sources = [s for s in source_list if isinstance(s, int)]
        if int_sources:
            expected = set(range(min(int_sources), max(int_sources) + 1))
            missing = expected - set(int_sources)
            for m in sorted(missing):
                violations.append(f"来源 {m} 未被任何字幕引用")

    # ④ 同 layer 内无重叠
    by_layer: dict[Any, list[dict]] = {}
    for s in subtitles:
        layer = s.get("layer", 0)
        by_layer.setdefault(layer, []).append(s)
    for layer, segs in by_layer.items():
        segs_sorted = sorted(segs, key=_t0)
        for i in range(1, len(segs_sorted)):
            prev_s = segs_sorted[i - 1]
            cur_s = segs_sorted[i]
            if _t0(cur_s) < _t1(prev_s) - 1e-6:
                label_p = prev_s.get("text") or prev_s.get("id") or f"#{i-1}"
                label_c = cur_s.get("text") or cur_s.get("id") or f"#{i}"
                violations.append(
                    f"同层重叠（layer={layer}）：「{label_p}」[{_t0(prev_s):.3f},{_t1(prev_s):.3f}) "
                    f"与「{label_c}」[{_t0(cur_s):.3f},{_t1(cur_s):.3f}) 时间交叉")

    return {"valid": len(violations) == 0, "violations": violations}
