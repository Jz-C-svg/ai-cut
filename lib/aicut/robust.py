r"""稳定性加固层（v4.0.0 最终版 · 上线零纰漏）。

职责：
  · 原子写入 —— 草稿/状态/配置一律先写临时文件再 os.replace，杜绝"剪映读到半成品"
  · 输入校验 —— 素材格式/时长/可读性，错误给中文可操作提示
  · 断点续跑 —— 全流程 step 状态文件，失败后可 --resume 继续
  · 统一异常 —— 常见故障分类映射为中文用户提示 + 日志落盘，绝不静默失败

用法：
    from .robust import atomic_write_json, validate_video_input, StepTracker, safe_call
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

LOG_ROOT = Path(__file__).resolve().parents[2] / "reports"
RESUME_DIR = Path(__file__).resolve().parents[2] / "work" / "resume"


# ---------- 原子写入 ----------

def atomic_write_json(path: str | Path, data: Any, *, pretty: bool = True) -> Path:
    """先写同目录临时文件，再 os.replace 原子替换。失败不破坏原文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ---------- 输入校验 ----------

def validate_video_input(path: str | Path) -> tuple[bool, str]:
    """校验素材视频：存在 / 可读 / ffprobe 可解析 / 时长 > 0。

    返回 (ok, 中文提示)。ok=False 时提示可直接展示给用户。
    """
    path = Path(path)
    if not path.exists():
        return False, f"素材不存在：{path}\n请检查路径是否正确（可拖动文件到窗口或手动输入完整路径）。"
    if not path.is_file():
        return False, f"路径不是文件：{path}"
    size = path.stat().st_size
    if size == 0:
        return False, f"素材文件为空（0 字节）：{path.name}\n请确认文件未损坏。"
    if size > 8 * 1024 ** 3:
        return False, f"素材过大（{size / 1024 ** 3:.1f} GB），超出当前支持范围（≤8GB）。"
    # ffprobe 探测
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return False, (f"无法解析素材（ffprobe 失败）：{path.name}\n"
                           f"错误：{(out.stderr or out.stdout or '').strip()[:200]}\n"
                           f"该文件可能不是有效视频，或编码不受支持。")
        dur = float(json.loads(out.stdout or "{}").get("format", {}).get("duration", 0) or 0)
        if dur <= 0:
            return False, f"素材时长为 0 或无法读取：{path.name}"
        return True, f"素材有效（{dur:.1f} 秒）"
    except FileNotFoundError:
        return False, "未找到 ffprobe。请先安装 FFmpeg 并确保 ffprobe 在 PATH 中（设置页可配置）。"
    except Exception as exc:  # noqa: BLE001
        return False, f"素材校验异常：{type(exc).__name__} {exc}"


def validate_text_input(text: str, *, field: str = "文案", max_len: int = 5000) -> tuple[bool, str]:
    text = (text or "").strip()
    if not text:
        return False, f"{field}为空，请先输入内容。"
    if len(text) > max_len:
        return False, f"{field}过长（{len(text)} 字 > {max_len} 字），请精简后再试。"
    return True, f"{field}有效（{len(text)} 字）"


# ---------- 断点续跑 ----------

class StepTracker:
    """全流程步骤状态机：记录已完成的步骤，支持 --resume 跳过已完成步骤。

    状态文件：work/resume/<job_name>.json
      {"job": "...", "steps": {"normalize": "ok", "transcribe": "ok"},
       "updated_at": "..."}
    """

    def __init__(self, job_name: str):
        self.job = job_name
        self.path = RESUME_DIR / f"{job_name}.json"
        self.data: dict = {"job": job_name, "steps": {}, "updated_at": ""}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                self.data.setdefault("steps", {})
        except Exception:  # noqa: BLE001
            self.data = {"job": self.job, "steps": {}, "updated_at": ""}

    def is_done(self, step: str) -> bool:
        return self.data.get("steps", {}).get(step) == "ok"

    def mark_done(self, step: str) -> None:
        self.data.setdefault("steps", {})[step] = "ok"
        self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        RESUME_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, self.data)

    def mark_failed(self, step: str, error: str) -> None:
        self.data.setdefault("steps", {})[step] = f"failed: {error[:200]}"
        self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        RESUME_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, self.data)

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def done_steps(self) -> list[str]:
        return [k for k, v in self.data.get("steps", {}).items() if v == "ok"]

    def failed_steps(self) -> dict[str, str]:
        return {k: v for k, v in self.data.get("steps", {}).items()
                if isinstance(v, str) and v.startswith("failed")}


# ---------- 统一异常与日志 ----------

def ensure_log_dir() -> Path:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    return LOG_ROOT


def log_error(where: str, exc: BaseException) -> Path:
    """错误落盘：reports/errors-YYYYMMDD.log，追加式。返回日志路径。"""
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    fname = f"errors-{time.strftime('%Y%m%d')}.log"
    line = (f"[{ts}] [{where}] {type(exc).__name__}: {exc}\n"
            + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-8:])
            + "-" * 60 + "\n")
    try:
        with open(LOG_ROOT / fname, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    return LOG_ROOT / fname


def friendly_error(where: str, exc: BaseException) -> str:
    """把常见异常映射为中文可操作提示。返回可直接展示给用户的字符串。"""
    msg = str(exc)
    low = msg.lower()
    # 本地模型/服务类
    if "11434" in msg or "ollama" in low or "connect" in low and "refused" in low:
        return (f"【{where}】本地 AI 服务未连接。\n"
                f"请先启动 Ollama（运行 ollama serve，或在设置页点击'启动服务'），"
                f"再重试。\n详情：{msg[:200]}")
    if "no such file" in low or "not found" in low or "不存在" in msg:
        return f"【{where}】文件或路径不存在，请检查后重试。\n详情：{msg[:200]}"
    if "timeout" in low or "timed out" in low:
        return (f"【{where}】操作超时（可能是网络或模型响应慢）。\n"
                f"可重试；如反复超时，请在设置页降低模型规格或检查网络。\n详情：{msg[:200]}")
    if "key" in low and ("api" in low or "auth" in low):
        return (f"【{where}】API 密钥缺失或无效。\n"
                f"请在设置页配置对应的 API Key（或设置环境变量），或改用本地模式。\n详情：{msg[:200]}")
    if "memory" in low or "cuda" in low or "out of" in low:
        return (f"【{where}】显存/内存不足。\n"
                f"请关闭其他占用程序，或在设置页切换更小的模型/降低 num_ctx。\n详情：{msg[:200]}")
    return f"【{where}】出现异常：{type(exc).__name__} {msg[:200]}\n已记录到日志，可重试或查看 reports/ 下错误日志。"


def safe_call(name: str, fn: Callable, *args, **kwargs) -> tuple[bool, Any]:
    """统一包装：成功返回 (True, 结果)；失败写日志并返回 (False, 中文提示)。"""
    try:
        return True, fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log_error(name, exc)
        return False, friendly_error(name, exc)


# ---------- 兜底：磁盘空间检查 ----------

def check_disk_space(path: str | Path, min_free_gb: float = 2.0) -> tuple[bool, str]:
    """成片/导出前检查目标盘剩余空间（默认至少 2GB）。"""
    import shutil
    try:
        free = shutil.disk_usage(str(Path(path).anchor or ".")).free / 1024 ** 3
        ok = free >= min_free_gb
        tip = f"磁盘剩余 {free:.1f}GB（需要 ≥{min_free_gb}GB）"
        return (True, tip) if ok else (False, f"{tip}，空间不足请清理或更换输出盘。")
    except Exception as exc:  # noqa: BLE001
        return True, f"磁盘检查跳过（{type(exc).__name__}）"
