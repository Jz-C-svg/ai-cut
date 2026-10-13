"""GLM-4.6V-Flash 9B 本地深度理解后端（llama-cli 直调）。

实测成功路径（三要素缺一不可）：
  1. .bat 临时文件包装执行（subprocess 直启交互式 CLI 会挂起/超时）
  2. prompt 用 `-f 文件` 传入（UTF-8 写 prompt 文件，彻底避开 cmd 引号/中文编码问题）
  3. 输出重定向到文件再读取（管道捕获会撑满缓冲）

实测性能（RTX 4060 8GB）：
  - 单帧：约 3 分钟（含加载 1-2 分钟），显存峰值 ~7GB
  - 原生视频：--video + --video-fps 2，解码+理解正常，能理解时序动作
  - mmproj 已量化 Q8_0（1704MB→931MB，视觉理解无损）

用途：素材关键帧/视频片段的深度语义理解，输出结构化 JSON：
    {"内容": "...", "类型": "...", "干净度": "...", "建议字幕": "...", ...}

与 llm_router 的关系：作为 type="glm-local" 后端被 llm_chat 调用（vision 任务）。
"""

from __future__ import annotations
from aicut.portability import portable_path

import json
import os
import re
import subprocess
import tempfile
import sys
import time
from pathlib import Path

# Bundled model paths are resolved relative to the project/install directory.
_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
_MODEL_CONFIG = _ROOT / "config" / "bundled_models.json"


def _bundled_paths() -> tuple[str, str, str]:
    try:
        data = json.loads(_MODEL_CONFIG.read_text(encoding="utf-8-sig"))
        item = data.get("vision") or {}
        base = _ROOT
        return (
            str(base / item["model"]),
            str(base / item["mmproj"]),
            str(base / item["runner"]),
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return "", "", ""


_BUNDLED_MODEL, _BUNDLED_MMPROJ, _BUNDLED_RUNNER = _bundled_paths()


def _prefer_bundled(bundled: str, external: str) -> str:
    return bundled if bundled and Path(bundled).exists() else external

# ---- 路径与参数（可用环境变量覆盖）------------------------------------------------
LLAMA_CLI = os.environ.get(
    "GLM_LLAMA_CLI",
    _prefer_bundled(_BUNDLED_RUNNER, portable_path('project:models/llama-cpp/llama-cli.exe')),
)
MODEL = os.environ.get(
    "GLM_MODEL",
    _prefer_bundled(_BUNDLED_MODEL, portable_path('project:models/glm46v-flash/GLM-4.6V-Flash-Q4_K_M.gguf')),
)
_MMPROJ_F16 = os.environ.get("GLM_MMPROJ_F16", portable_path('project:models/glm46v-flash/mmproj-F16.gguf'))
_MMPROJ_Q8 = os.environ.get("GLM_MMPROJ_Q8", portable_path('project:models/glm46v-flash/mmproj-Q8_0.gguf'))
_NGL = int(os.environ.get("GLM_NGL", "85"))        # 8GB 卡存活关键
_CTX = int(os.environ.get("GLM_CTX", "16384"))     # 视频多帧需要，8GB 卡仍可控
_THREADS = int(os.environ.get("GLM_THREADS", "8"))
_TIMEOUT = int(os.environ.get("GLM_TIMEOUT", "600"))


def mmproj() -> str:
    """优先用 Q8_0 量化版（931MB，实测无损且更快）；不存在时回退 F16。"""
    if _BUNDLED_MMPROJ and Path(_BUNDLED_MMPROJ).exists():
        return _BUNDLED_MMPROJ
    if Path(_MMPROJ_Q8).exists():
        return _MMPROJ_Q8
    return _MMPROJ_F16


# 结构化理解提示词（实测输出四字段 JSON）
DEFAULT_PROMPT = (
    "你是视频剪辑助手。请分析这张图片画面，直接输出 JSON 结果，不要任何思考过程。"
    '{"内容":"画面中有什么人物/场景/物体，简洁概括","类型":"视频类型判断（口播/教程/展示/剧情/其他）",'
    '"干净度":"高/中/低及简要理由","建议字幕":"适合叠加在画面上的字幕文案"}'
)

# 深度语义理解（用于素材理解环节，比 DEFAULT_PROMPT 更细）
DEEP_PROMPT = (
    "你是视频剪辑策划。仔细分析这张素材画面的语义，直接输出 JSON 不要思考过程："
    '{"主体":"画面主体人物/物体","场景":"场景与氛围","动作":"正在发生的动作",'
    '"画面干净度":"高/中/低（画面是否杂乱/有杂物/构图问题）","建议画面":"此素材适合搭配什么画面或字幕",'
    '"剪辑建议":"适合的剪辑手法（转场/卡点/字幕样式）"}'
)

# 视频理解提示词（用 --video 输入，模型能理解时序动作）
VIDEO_PROMPT = (
    "你是视频剪辑策划。分析这个视频片段（含时序动作），直接输出 JSON 不要思考过程："
    '{"内容":"画面中有什么人物/场景/物体，简洁概括","类型":"口播/教程/展示/剧情/其他",'
    '"动作":"正在发生的动作/情节","干净度":"高/中/低及理由","剪辑建议":"适合的剪辑手法"}'
)


def _quote_args(args: list[str]) -> list[str]:
    """为 cmd 行引用参数（含空格/特殊字符的加引号）。"""
    out = []
    for a in args:
        if any(ch in a for ch in " \t\"&|<>^"):
            out.append('"' + a.replace('"', '""') + '"')
        else:
            out.append(a)
    return out


def _extract_json(text: str) -> dict:
    """从 llama-cli 输出中稳健提取 JSON。"""
    if not text:
        return {"error": "empty-output"}
    # 1) ```json 围栏块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        return _safe_load(m.group(1))
    # 2) 用 JSON decoder 找完整对象，支持数组/对象嵌套及前置 thinking。
    decoder = json.JSONDecoder()
    candidates: list[dict] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    if candidates:
        required = ("主角", "场景", "关键信息点", "高光候选")

        def score(item: tuple[int, dict]) -> tuple[int, int, int]:
            index, value = item
            present = sum(bool(value.get(key)) for key in required)
            usable = sum(
                bool(str(key).strip("? ")) and value.get(key) not in (None, "", [])
                for key in value
            )
            return present, usable, index

        return max(enumerate(candidates), key=score)[1]
    # 3) 失败：返回原文尾部
    return {"error": "no-json", "raw": text[-800:]}


def _safe_load(s: str) -> dict:
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    try:
        return json.loads(s.replace("'", '"'))
    except Exception:
        return {"error": "parse-fail", "raw": s[:600]}


def _run_llama(
    media_args: list[str],
    prompt: str,
    max_tokens: int,
    temperature: float,
    timeout: int | None,
    tag: str,
    cancel_event=None,
    persist_diagnostic: bool = True,
) -> dict:
    """核心执行：写 UTF-8 prompt 文件 → GBK bat 包装 → 文件重定向 → 提取 JSON。

    实测证明这是本机唯一可靠的 llama-cli 调用方式。
    """
    cmd = [
        LLAMA_CLI,
        "-m", MODEL,
        "--mmproj", mmproj(),
        "-ngl", str(_NGL),
        "-c", str(_CTX),
        "-t", str(_THREADS),
        *media_args,
    ]
    work = Path(tempfile.gettempdir()) / f"glm_{tag}_{os.getpid()}"
    prompt_file = work.with_suffix(".txt")
    bat = work.with_suffix(".bat")
    out_file = work.with_suffix(".log")
    try:
        # 1) prompt 写 UTF-8 文件，用 -f 传入（避开 cmd 引号/中文编码问题）
        prompt_file.write_text(prompt, encoding="utf-8")
        full_cmd = cmd + [
            "-f", str(prompt_file), "-n", str(max_tokens),
            "--temp", str(temperature), "--reasoning", "off",
            "--no-display-prompt",
        ]
        line = '"{}" {} > "{}" 2>&1'.format(
            LLAMA_CLI, " ".join(_quote_args(full_cmd[1:])), out_file)
        # 2) bat 用 GBK 写（cmd 按 ANSI 解析）
        bat.write_text(line + "\r\n", encoding="utf-8")
        # 3) 执行
        run_timeout = timeout or _TIMEOUT
        if os.name == "nt":
            # A .bat launches llama-cli as a child. subprocess.run(timeout=...)
            # only kills cmd.exe and can leave the multi-GB model process alive.
            proc = subprocess.Popen(
                [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(bat)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + run_timeout
            try:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("视觉分析已取消")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(proc.args, run_timeout)
                    try:
                        proc.wait(timeout=min(0.5, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except (subprocess.TimeoutExpired, InterruptedError):
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if cancel_event is not None and cancel_event.is_set():
                    return {"error": "cancelled"}
                raise
        else:
            subprocess.run([str(bat)], stdin=subprocess.DEVNULL, timeout=run_timeout)
        text = out_file.read_text(encoding="utf-8", errors="replace")
        out = _extract_json(text)
        if persist_diagnostic:
            diagnostic = Path(__file__).resolve().parents[2] / "work" / f"glm-{tag}-last.log"
            diagnostic.parent.mkdir(parents=True, exist_ok=True)
            diagnostic.write_text(text[-12000:], encoding="utf-8")
        if out.get("error"):
            pass
        out["_model"] = "glm-4.6v-flash"
        return out
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:300]}
    finally:
        for f in (prompt_file, bat, out_file):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass


def analyze_frame(
    image: str | Path,
    prompt: str | None = None,
    max_tokens: int = 600,
    temperature: float = 0.2,
    timeout: int | None = None,
    cancel_event=None,
    persist_diagnostic: bool = True,
) -> dict:
    """对单张图片做深度理解，返回结构化 dict（实测约 3 分钟/帧含加载）。"""
    image = str(image)
    if not Path(image).exists():
        return {"error": "image-not-found", "image": image}
    out = _run_llama(
        ["--image", image], prompt or DEFAULT_PROMPT,
        max_tokens, temperature, timeout, "frame", cancel_event, persist_diagnostic,
    )
    out["_image"] = image
    return out


def analyze_video(
    video: str | Path,
    prompt: str | None = None,
    video_fps: float = 0.25,
    max_tokens: int = 600,
    temperature: float = 0.2,
    timeout: int | None = None,
) -> dict:
    """通过时序联系图理解视频，避免原生视频 token 在本机上下文中爆炸。"""
    video = str(video)
    if not Path(video).exists():
        return {"error": "video-not-found", "video": video}
    sheet = Path(tempfile.gettempdir()) / f"glm_video_sheet_{os.getpid()}.jpg"
    fps = max(0.1, min(float(video_fps), 1.0))
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", video,
             "-vf", f"fps={fps},scale=640:-2,tile=2x2:padding=4:margin=4",
             "-frames:v", "1", str(sheet)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0 or not sheet.exists():
            return {"error": "video-contact-sheet-failed",
                    "detail": (proc.stderr or "")[-300:]}
        out = _run_llama(
            ["--image", str(sheet)],
            (prompt or VIDEO_PROMPT)
            + "\n这是按时间顺序排列的四格视频联系图，请结合四格画面判断变化。",
            max_tokens, temperature, timeout, "video",
        )
        out["_video_mode"] = "contact-sheet"
    finally:
        sheet.unlink(missing_ok=True)
    out["_video"] = video
    return out


def analyze_text(
    prompt: str,
    max_tokens: int = 400,
    temperature: float = 0.2,
    timeout: int | None = None,
) -> dict:
    """纯文本模式（无图片/视频），用于 llm_router 的 glm-local 文本/决策任务。"""
    return _run_llama([], prompt, max_tokens, temperature, timeout, "text")


def analyze_frames(
    images: list[str | Path],
    prompt: str | None = None,
    max_tokens: int = 600,
    temperature: float = 0.2,
    timeout: int | None = None,
) -> list[dict]:
    """批量分析多帧（逐帧直调；8GB 卡每帧约 3 分钟）。"""
    return [analyze_frame(img, prompt, max_tokens, temperature, timeout) for img in images]


def is_available() -> bool:
    return (Path(LLAMA_CLI).exists() and Path(MODEL).exists()
            and Path(mmproj()).exists())


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python glm_deep.py <图片路径> [--deep|--video]")
        sys.exit(1)
    arg = sys.argv[1]
    use_deep = "--deep" in sys.argv
    as_video = "--video" in sys.argv
    if as_video:
        print(json.dumps(analyze_video(arg, DEEP_PROMPT if use_deep else VIDEO_PROMPT),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(analyze_frame(arg, DEEP_PROMPT if use_deep else DEFAULT_PROMPT),
                         ensure_ascii=False, indent=2))
