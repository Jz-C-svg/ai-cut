r"""素材准备类技能：把原片变成流水线能吃的形态。

原片直接进 auto_edit 会过不了质量门（没有逐字稿就没有封面承诺、没有句子切点），
所以"投素材自动开工"的正确顺序是：

  标准化（1080x1920 / 抽音轨）-> 转写 -> （可选）识别 -> 按风格出片

这些步骤都调用既有脚本，不重复实现；主体负责排序、检查结果和如实汇报。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import skills
from .paths import ROOT, SCRIPTS, python_exe


def _run(args: list, timeout: int = 3600) -> dict:
    proc = subprocess.run(
        [python_exe(), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    return {
        "rc": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-600:],
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def skill_prepare_material(ctx: skills.Context, args: dict) -> dict:
    """标准化 + 转写（+ 可选识别），让后续出片有句子和封面可用。"""
    video = Path(args["video"]) if args.get("video") else ctx.video
    if not video or not Path(video).exists():
        return {"op": "prepare_material", "ok": False, "error": "没有可用的素材文件"}
    name = args.get("name") or Path(video).stem
    norm = ROOT / "work" / "normalized" / f"{name}.mp4"
    audio = ROOT / "01-audio" / f"{name}.wav"
    transcript = ROOT / "02-transcript" / f"{name}.json"

    report: dict = {"op": "prepare_material", "video": str(video), "name": name, "steps": []}

    if not norm.exists():
        res = _run([SCRIPTS / "preprocess.py", video, "--out-name", name])
        report["steps"].append({"标准化": res["rc"]})
        report.update({k: v for k, v in res.items() if k != "rc"})
        if res["rc"] != 0 and not norm.exists():
            report["ok"] = False
            report["error"] = f"标准化失败（rc={res['rc']}）"
            return report
    else:
        report["steps"].append({"标准化": "已存在"})

    if transcript.exists():
        report["steps"].append({"转写": "已有逐字稿"})
    else:
        if not audio.exists():
            report["ok"] = False
            report["error"] = f"缺少音轨 {audio.name}，无法转写"
            return report
        res = _run([SCRIPTS / "transcribe.py", audio, "--name", name])
        report["steps"].append({"转写": res["rc"]})
        report.update({k: v for k, v in res.items() if k != "rc"})
        if not transcript.exists():
            report["ok"] = False
            report["error"] = f"转写没产出逐字稿（rc={res['rc']}）"
            return report

    if args.get("understand"):
        res = _run([SCRIPTS / "understand.py", "--video", norm, "--out", str(ROOT / "work" / f"understand-{name}.json")])
        report["steps"].append({"识别": res["rc"]})

    report["ok"] = True
    report["note"] = (f"{name} 已就绪："
                      + ("逐字稿齐" if transcript.exists() else "无逐字稿")
                      + (f"，标准化 {norm.name}" if norm.exists() else ""))
    report["result_material"] = str(norm if norm.exists() else video)
    return report


skills.register(skills.Skill(
    id="prepare_material",
    goal="把原片标准化并转写，为出片准备好句子与封面素材",
    channel="shell",
    args={"video": "素材路径（默认取上下文里的）", "name": "素材名（默认取文件名）",
          "understand": "是否顺便跑 AI 识别，true/false"},
    sample={"understand": False},
    fn=skill_prepare_material,
    notes="出片前的一步；已有逐字稿会自动跳过转写",
))
