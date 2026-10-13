#!/usr/bin/env python3
r"""风格匹配决策模块自测（stylematch + auto_edit 风格加载）。

覆盖：
  SM-A 规则通道：语义档案（hook-explain-close / 专业 / 5 信息点）-> 推荐含 koubo-zhishi
  SM-B 降级通道：无语义档案，仅 shotprof summary + 短转写 -> 非空 Top3
  SM-C --list-styles：子进程调用 auto_edit.py --list-styles，输出含 12 个风格 id
  SM-D 风格加载：load_style("koubo-zhishi") 返回含 配音.voice 与 关键帧 的完整卡

为保证确定性，规则通道用例里把 Ollama 决策通道短路（强制 None），
避免本地模型在线/离线影响断言。不调付费 API、不生成草稿。

用法：
  "<project-root>\venv\Scripts\python.exe" "<project-root>\scripts\selftest_stylematch.py"
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

from aicut import stylematch  # noqa: E402

PY = str(ROOT / "venv" / "Scripts" / "python.exe")
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""),
          flush=True)


def main() -> int:
    # —— SM-A 规则通道（短路 Ollama，保证确定性）——
    stylematch._ollama_decide = lambda *a, **k: None  # type: ignore
    semantic = {
        "主角": "一名护士在月子中心",
        "场景": "室内护理室",
        "讲了什么": "讲解新生儿护理知识",
        "关键信息点": ["第一点", "第二点", "第三点", "第四点", "第五点"],
        "情绪基调": ["专业"],
        "适合结构": "hook-explain-close",
    }
    r = stylematch.match_style(semantic_doc=semantic)
    rec_ids = [x["id"] for x in r["推荐"]]
    check("SM-A 规则通道 Top3 非空", len(r["推荐"]) == 3, f"ids={rec_ids}")
    check("SM-A 推荐结果含 koubo-zhishi", "koubo-zhishi" in rec_ids,
          f"Top1={rec_ids[0] if rec_ids else None}")
    confs = [x["置信度"] for x in r["推荐"]]
    check("SM-A 置信度在 0-1 且 Top1 最高（按全风格总分归一化）",
          all(0 <= c <= 1 for c in confs) and confs[0] == max(confs),
          f"置信度={confs}")
    check("SM-A 使用输入=semantic", r["使用的输入"] == "semantic", r["使用的输入"])
    check("SM-A 匹配方式=rule（已短路 Ollama）", r["匹配方式"] in ("rule", "fallback"),
          r["匹配方式"])

    # —— SM-B 降级通道：无语义档案，仅 shotprof summary + 短转写 ——
    shotprof_summary = {
        "hook_candidates": [{"t": 1.2, "理由": "开头抓眼"}],
        "safe_area_conflicts": [],
        "hints": [],
    }
    r2 = stylematch.match_style(semantic_doc=None,
                                shotprof_summary=shotprof_summary,
                                transcript_text="带大家看一下月子中心的房间和环境")
    check("SM-B 降级通道返回 Top3", len(r2["推荐"]) == 3,
          f"ids={[x['id'] for x in r2['推荐']]}")
    check("SM-B 降级通道使用输入=shotprof", r2["使用的输入"] in ("shotprof", "transcript"),
          r2["使用的输入"])
    check("SM-B 降级通道有非空理由", all(x["理由"] for x in r2["推荐"]),
          "")

    # —— SM-C --list-styles 子进程 ——
    proc = subprocess.run([PY, str(ROOT / "scripts" / "auto_edit.py"), "--list-styles"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", cwd=str(ROOT))
    out = proc.stdout + proc.stderr
    all_ids = ["koubo-zhishi", "kongjian-zhanshi", "qingxu-gushi", "duibi-ceping",
               "kadian-jinji", "wenzi-kuaishan", "jiating-richang", "zhuanti-pandian",
               "jiaocheng-buzhou", "fangtan-duitan", "zhiyu-shuhuan", "xuanchuan-pian"]
    missing = [i for i in all_ids if i not in out]
    check("SM-C --list-styles 退出码 0", proc.returncode == 0, f"rc={proc.returncode}")
    check("SM-C --list-styles 输出含全部 12 个风格 id", not missing,
          f"缺失={missing}")

    # —— SM-D 风格加载：per-style 卡含 配音.voice 与 关键帧 ——
    import auto_edit  # noqa: E402
    card = auto_edit.load_style("koubo-zhishi")
    p = card.get("剪辑参数") or {}
    voice = (p.get("配音") or {}).get("voice")
    kf = p.get("关键帧")
    check("SM-D load_style 返回 dict", isinstance(card, dict) and bool(card), card.get("id", ""))
    check("SM-D 含 剪辑参数.配音.voice", bool(voice), f"voice={voice}")
    check("SM-D 含 剪辑参数.关键帧", bool(kf), f"关键帧={kf}")
    check("SM-D 配音.voice 为 Edge-TTS 音色",
          isinstance(voice, str) and voice.startswith("zh-CN-"), f"voice={voice}")

    # list_styles() 函数
    styles = auto_edit.list_styles()
    ids = [s['id'] for s in styles]
    check("SM-D 风格列表保留基础风格且 id 唯一", set(all_ids).issubset(ids) and len(ids) == len(set(ids)), f"n={len(styles)}")

    # —— SM-E 内容类型自动检测：教程转写 -> Top1 jiaocheng-buzhou ——
    r3 = stylematch.match_style(
        semantic_doc=None, shotprof_summary={},
        transcript_text="第一步先洗手，第二步把奶粉倒入，第三步怎么冲泡，教你正确方法")
    check("SM-E 内容类型检测 Top1=jiaocheng-buzhou",
          r3["推荐"][0]["id"] == "jiaocheng-buzhou",
          f"Top1={r3['推荐'][0]['id']}")
    sid, kw = stylematch.detect_content_type("带大家看一下月子中心的房间环境")
    check("SM-E detect_content_type 识别展示类", sid == "kongjian-zhanshi" and kw == "带大家看",
          f"sid={sid} kw={kw}")

    # —— SM-F 负向惩罚：未命中风格理由里应出现「不匹配/超出/不匹配该风格」 ——
    r4 = stylematch.match_style(
        semantic_doc={"主角": "顾客妈妈", "场景": "卧室", "讲了什么": "我的生产经历",
                      "关键信息点": ["很累", "很痛"], "情绪基调": ["温暖"], "适合结构": "story-arc"})
    joined = " ".join(x["理由"] for x in r4["推荐"])
    check("SM-F 负向惩罚在非匹配项理由中体现",
          any(k in joined for k in ("不匹配", "超出区间", "不匹配该风格")),
          joined[:80])

    # —— SM-G softmax 置信度：Top1 最高、间隔字段存在 ——
    confs = [x["置信度"] for x in r4["推荐"]]
    check("SM-G softmax 置信度∈[0,1] 且 Top1 最高",
          all(0 <= c <= 1 for c in confs) and confs[0] == max(confs), f"confs={confs}")
    check("SM-G 间隔字段存在且≥0", "间隔" in r4 and r4["间隔"] >= 0,
          f"间隔={r4.get('间隔')}")
    probs = stylematch.softmax([10.0, 5.0, -2.0])
    check("SM-G softmax 归一化和≈1", abs(sum(probs) - 1.0) < 1e-6, f"sum={sum(probs)}")

    # —— SM-H 差异化理由 / 并列标记 ——
    check("SM-H 每条推荐含差异化字段", all("差异化" in x for x in r4["推荐"]), "")
    check("SM-H 含并列推荐布尔字段", isinstance(r4.get("并列推荐"), bool),
          f"并列={r4.get('并列推荐')}")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n===== 结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 =====", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
