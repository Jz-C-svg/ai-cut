r"""v4.0.0 上线稳定性加固（P0 核心）。

只依赖标准库，独立成模块，不与 v3.8/v3.9 的文件耦合。提供五块能力：

  (a) DEGRADATION_MATRIX 全链路降级矩阵 + execute_step 统一包装
  (b) atomic_write_json 原子写 JSON（tmp + fsync + os.replace）
  (c) BatchProgress 批处理幂等与断点续跑
  (d) validate_config 配置校验与默认值补全
  (e) format_error 中文可读错误格式化

设计约定：
  · 本模块不 import 任何 aicut 子模块（尤其不 import paths），
    以免 paths.config() -> resilience.validate_config() 形成循环导入。
    工作目录一律从 __file__ 自行定位。
  · execute_step 永不向外抛异常（恒返回三元组），是否终止由调用方按 fatal 标志决定。
"""
from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# 工程定位：lib/aicut/resilience.py -> ROOT（不依赖 paths，避免循环导入）
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent.parent  # lib/aicut -> lib -> ROOT
WORK_DIR = _ROOT / "work"
ERROR_LOG = WORK_DIR / "error_log.txt"
PROGRESS_FILE = WORK_DIR / "batch_progress.json"


# ---------------------------------------------------------------------------
# (a) 全链路降级矩阵
# ---------------------------------------------------------------------------
# on_fail: skip=跳过该步继续 / degrade=用降级方案 / retry=重试 / terminate=终止
DEGRADATION_MATRIX: dict[str, dict[str, Any]] = {
    "normalize":   {"on_fail": "skip",   "retries": 1, "fatal": False},
    "transcribe":  {"on_fail": "degrade", "retries": 1, "fatal": False,
                    "fallback": "use_srt_or_empty"},
    "shotprof":    {"on_fail": "degrade", "retries": 1, "fatal": False,
                    "fallback": "uniform_shots"},
    "semantic":    {"on_fail": "skip",    "retries": 0, "fatal": False},
    "style":       {"on_fail": "degrade", "retries": 1, "fatal": False,
                    "fallback": "default_style"},
    "visualmap":   {"on_fail": "skip",    "retries": 0, "fatal": False},
    "draft_write": {"on_fail": "retry",   "retries": 2, "fatal": True},
    "export":      {"on_fail": "skip",    "retries": 1, "fatal": False},
}

# 未登记步骤的兜底策略：不重试、不崩溃、跳过继续
_DEFAULT_POLICY = {"on_fail": "skip", "retries": 0, "fatal": False}


# ---------------------------------------------------------------------------
# (e) 中文错误格式化 + 错误日志
# ---------------------------------------------------------------------------
_TYPE_CN: dict[type, str] = {
    FileNotFoundError: "文件找不到",
    PermissionError: "没有文件读写权限",
    IsADirectoryError: "路径指向了目录而非文件",
    json.JSONDecodeError: "JSON 解析失败",
    UnicodeDecodeError: "文件编码不是 UTF-8",
    TimeoutError: "操作超时",
    KeyError: "配置/字段缺失",
    ValueError: "数值或参数不合法",
    OSError: "系统或 IO 错误",
}


def format_error(exc: Exception, step: str = "") -> str:
    """把异常转成中文可读错误：步骤名 + 错误类型中文 + 关键信息。"""
    cn = _TYPE_CN.get(type(exc), type(exc).__name__)
    step_part = f"【步骤 {step}】" if step else ""
    msg = str(exc) or f"({type(exc).__name__}，无消息)"
    return f"{step_part}{cn}（{type(exc).__name__}）: {msg}"


def log_error(step: str, exc: Exception) -> None:
    """追加一条错误记录到 work/error_log.txt（带时间戳+步骤名+错误类型+堆栈摘要）。

    追加写，绝不覆盖历史。文件与目录不存在时自动创建；写日志本身失败时静默吞掉，
    不能因为记日志失败而把主流程带崩。
    """
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        brief = format_error(exc, step)
        # 堆栈摘要：只取最后两帧，避免日志爆炸
        stack = "\n".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        stack_tail = "\n    ".join(stack.strip().splitlines()[-4:])
        line = f"[{ts}] [{step or '-'}] {brief}\n    {stack_tail}\n"
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001 - 记日志失败绝不能影响主流程
        pass


def execute_step(step_name: str, func: Callable[..., Any], *args: Any,
                 _fallback: Any = None, **kwargs: Any) -> tuple[bool, Any, str]:
    """统一包装一个全链路步骤。

    返回 (是否成功, 结果或降级产物/None, 中文可读错误信息)：
      · 成功：(True, func 返回值, "")
      · 降级：(False, 降级产物(非 None), "…已降级：原因")
      · 跳过：(False, None, 原因)
      · 重试耗尽：按 on_fail 落到 skip/degrade；恒不向外抛异常。

    降级产物通过关键字 `_fallback` 传入（可调用或具体值），它不会被转发给 func。
    fatal=True 的步骤失败时，调用方应自行决定终止（本函数只返回，不替调用方做决定）。
    """
    policy = DEGRADATION_MATRIX.get(step_name, _DEFAULT_POLICY)
    on_fail = str(policy.get("on_fail", "skip"))
    retries = int(policy.get("retries", 0))
    attempts = max(1, retries + 1)

    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            result = func(*args, **kwargs)
            if i > 0:
                log_error(step_name, RuntimeError(f"第 {i} 次重试后成功"))
            return True, result, ""
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log_error(step_name, exc)
            if i < attempts - 1:
                # 还有重试机会，继续（retry 策略由 attempts 次数控制）
                continue
            break

    # —— 重试耗尽，按策略收尾 ——
    msg = format_error(last_exc, step_name) if last_exc else f"步骤 {step_name} 失败（未知原因）"

    if on_fail == "degrade":
        fb = policy.get("fallback", "")
        try:
            if callable(_fallback):
                degraded = _fallback(*args, **kwargs)
            else:
                degraded = _fallback
            if degraded is not None:
                return False, degraded, f"{msg}（已按降级方案处理：{fb}）"
        except Exception as exc:  # noqa: BLE001
            log_error(step_name, exc)
        return False, None, f"{msg}（降级方案 {fb} 也失败，按跳过处理）"

    # skip / retry(耗尽) / terminate：都返回失败+None，由调用方按 fatal 决定是否终止
    return False, None, msg


# ---------------------------------------------------------------------------
# (b) 原子写入工具
# ---------------------------------------------------------------------------
def atomic_write_json(path: str | Path, data: dict, *, indent: int | None = None,
                      ensure_ascii: bool = False) -> Path:
    """原子写入 JSON：先写临时文件 .tmp，fsync 落盘，再 os.replace 原子改名。

    中断只会留下一个 .tmp 临时文件，目标文件要么是旧的完整版本，要么是新的完整版本，
    绝不会出现"写了一半"的 draft_content.json。失败时清理临时文件。

    indent=None 时用紧凑分隔符 (",", ":")，与历史 Draft.save 输出逐字节一致。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if indent is None:
        text = json.dumps(data, ensure_ascii=ensure_ascii, separators=(",", ":"))
    else:
        text = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
    payload = text.encode("utf-8")

    tmp = path.with_name(path.name + ".tmp" + uuid.uuid4().hex[:8])
    try:
        # 1) 写临时文件并强制落盘（fsync 确保持久，不是只在系统缓存里）
        with open(tmp, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        # 2) 原子改名（Windows 上 os.replace 同卷覆盖是原子的）
        os.replace(tmp, path)
    except Exception:
        # 失败清理临时文件，避免残留
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return path


def find_tmp_candidates(path: str | Path) -> list[Path]:
    """找出目标文件对应的所有 .tmp 残留（恢复用）。"""
    p = Path(path)
    return sorted(p.parent.glob(f"{p.name}.tmp*"))


# ---------------------------------------------------------------------------
# (c) 批处理进度追踪
# ---------------------------------------------------------------------------
class BatchProgress:
    """批处理幂等与断点续跑。

    进度记录到 work/batch_progress.json：
      {"job_id": "...", "updated": "...",
       "items": {"素材路径": {"status": "completed|failed|started",
                            "output": "...", "error": "...", "ts": "..."}}}

    每个状态变更都走 atomic_write_json，中断不会写坏进度文件。
    """

    def __init__(self, job_id: str):
        self.job_id = str(job_id)
        self.path = PROGRESS_FILE
        self.data = self._read()

    # -- 内部 --
    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if isinstance(d, dict) and d.get("job_id") == self.job_id and isinstance(d.get("items"), dict):
                return d
        except Exception:  # noqa: BLE001
            pass
        # 文件不存在 / 损坏 / 属于别的 job：按新任务重建
        return {"job_id": self.job_id, "updated": "", "items": {}}

    def _save(self) -> None:
        self.data["updated"] = datetime.now().isoformat(timespec="seconds")
        atomic_write_json(self.path, self.data, indent=2, ensure_ascii=False)

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # -- 写接口 --
    def mark_started(self, material: str) -> None:
        self.data["items"][str(material)] = {
            "status": "started", "output": "", "error": "", "ts": self._now()}
        self._save()

    def mark_completed(self, material: str, output_path: str) -> None:
        self.data["items"][str(material)] = {
            "status": "completed", "output": str(output_path), "error": "", "ts": self._now()}
        self._save()

    def mark_failed(self, material: str, error: str) -> None:
        self.data["items"][str(material)] = {
            "status": "failed", "output": "", "error": str(error)[:500], "ts": self._now()}
        self._save()

    # -- 读接口 --
    def is_completed(self, material: str) -> bool:
        item = self.data["items"].get(str(material)) or {}
        return item.get("status") == "completed"

    def pending_materials(self, all_materials: list[str]) -> list[str]:
        """返回未完成的素材列表：只有 completed 才跳过，started/failed/缺失都重跑。"""
        out: list[str] = []
        for m in all_materials:
            item = self.data["items"].get(str(m)) or {}
            if item.get("status") != "completed":
                out.append(m)
        return out

    def reset(self) -> None:
        self.data = {"job_id": self.job_id, "updated": "", "items": {}}
        self._save()


# ---------------------------------------------------------------------------
# (d) 配置校验与默认值
# ---------------------------------------------------------------------------
DEFAULT_RENDER = {"width": 1080, "height": 1920, "fps": 30, "crf": 20}
DEFAULT_ASR = {"model": "small", "device": "cpu", "compute_type": "int8", "language": "zh"}
DEFAULT_VISION_MODEL = "qwen3-vl:8b"


def validate_config(cfg: dict) -> tuple[dict, list[str]]:
    """校验 aicut.json：缺失字段给默认值 + 警告，不崩溃。

    不修改原配置文件，也不原地改入参 dict（返回补全后的新 dict）。
    返回 (补全后的配置, 警告列表)。
    """
    warnings: list[str] = []
    if not isinstance(cfg, dict):
        cfg = {}
        warnings.append("配置根节点不是对象，已按空配置处理")

    out = dict(cfg)  # 浅拷贝，子 dict 深拷避免污染入参

    # —— 顶层必需字段 ——
    if not out.get("project"):
        out["project"] = "AI cut"
        warnings.append("缺少 project，已补默认「AI cut」")
    if not out.get("root"):
        out["root"] = str(_ROOT)
        warnings.append("缺少 root，已补工程根目录")
    if not out.get("python"):
        out["python"] = sys.executable
        warnings.append("缺少 python，已使用当前解释器")
    if not out.get("ffmpeg"):
        out["ffmpeg"] = "ffmpeg"
        warnings.append("缺少 ffmpeg，已补 PATH 默认")
    if not out.get("ffprobe"):
        out["ffprobe"] = "ffprobe"
        warnings.append("缺少 ffprobe，已补 PATH 默认")

    # —— render：缺字段给默认（1080x1920, 30fps, crf20）——
    render = out.get("render")
    if not isinstance(render, dict):
        render = {}
        warnings.append("缺少 render 段，已补默认 1080x1920 / 30fps / crf20")
    else:
        render = dict(render)
        missing = [k for k, v in DEFAULT_RENDER.items() if k not in render or render[k] in (None, "")]
        if missing:
            warnings.append(f"render 缺字段 {missing}，已补默认值")
    for k, v in DEFAULT_RENDER.items():
        render.setdefault(k, v)
    out["render"] = render

    # —— asr：缺字段给默认（small, cpu, int8, zh）——
    asr = out.get("asr")
    if not isinstance(asr, dict):
        asr = {}
        warnings.append("缺少 asr 段，已补默认 small/cpu/int8/zh")
    else:
        asr = dict(asr)
        missing = [k for k, v in DEFAULT_ASR.items() if k not in asr or asr[k] in (None, "")]
        if missing:
            warnings.append(f"asr 缺字段 {missing}，已补默认值")
    for k, v in DEFAULT_ASR.items():
        asr.setdefault(k, v)
    out["asr"] = asr

    # —— draft_root：缺失时仅警告，由 paths.draft_root() 回退剪映默认目录 ——
    if not out.get("draft_root"):
        warnings.append("缺少 draft_root，运行时将回退到剪映默认草稿目录")

    # —— ai_backends：缺失给空列表（router 自动构造默认 Ollama）——
    if "ai_backends" not in out or not isinstance(out.get("ai_backends"), list):
        out["ai_backends"] = []
        warnings.append("缺少 ai_backends，已置空列表（router 将自动构造默认 Ollama）")

    # —— vision.model：缺失给默认模型 ——
    vision = out.get("vision")
    if not isinstance(vision, dict):
        vision = {}
        out["vision"] = vision
    if not vision.get("model"):
        vision["model"] = DEFAULT_VISION_MODEL
        warnings.append(f"vision.model 缺失，已补默认 {DEFAULT_VISION_MODEL}")

    return out, warnings
