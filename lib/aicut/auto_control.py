r"""auto_control · 多剪辑软件自动操作执行层（官方接口优先 + UI 自动化兜底）。

A（官方脚本接口）：优先用各软件官方支持的自动化通道实际执行：
- aftereffects：`AfterFX.exe -r <script.jsx>`（AE 官方命令行脚本执行）
- premiere：生成 ExtendScript(.jsx)（PR 无官方外部 CLI，落盘后可由 CEP/用户触发）
- davinci：通过官方 fusionscript.dll（Resolve Scripting API）直连
- vegas：生成 C# 脚本（Vegas 内置脚本引擎运行）
B（UI 自动化兜底）：软件不支持脚本接口时，退回 ui_bridge 的界面自动化
（激活窗口 + 快捷键序列），复用项目既有 ui_cmd/ui_agent 毫秒级通道。

不使用任何进程注入：对闭源商业软件只走官方接口与前台窗口自动化。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from editor_bridges import registry  # noqa: E402
from editor_bridges.base import ControlResult  # noqa: E402

# 产物目录：自动生成的脚本都落这里，便于排查与复跑
OUT_DIR = ROOT / "work" / "auto-control"

# UI 自动化兜底：action → 快捷键序列（交给 scripts/ui_bridge.py run 执行）
_UI_KEYMAP: dict[str, str] = {
    "cut": "ctrl+b",
    "delete": "delete",
    "play": "space",
    "stop": "space",
    "undo": "ctrl+z",
    "save": "ctrl+s",
}

# 各软件安装目录探测（官方接口执行需要）
_INSTALL_GLOBS: dict[str, list[str]] = {
    "aftereffects": [r"C:\Program Files\Adobe\Adobe After Effects*"],
    "premiere": [r"C:\Program Files\Adobe\Adobe Premiere Pro*"],
    "davinci": [r"C:\Program Files\Blackmagic Design\DaVinci Resolve"],
    "vegas": [r"C:\Program Files\VEGAS\VEGAS Pro*", r"C:\Program Files\MAGIX\VEGAS Pro*"],
}

_PROCESS_NAMES: dict[str, str] = {
    "aftereffects": "AfterFX.exe",
    "premiere": "Adobe Premiere Pro.exe",
    "davinci": "Resolve.exe",
    "vegas": "vegas",
    "jianying": "JianyingPro.exe",
    "capcut": "CapCut.exe",
}


def _find_install(software_id: str) -> Path | None:
    for pattern in _INSTALL_GLOBS.get(software_id, []):
        base = Path(pattern).parent
        if not base.exists():
            continue
        matches = sorted(base.glob(Path(pattern).name))
        if matches:
            return matches[-1]
    return None


def _is_running(software_id: str) -> bool:
    name = _PROCESS_NAMES.get(software_id, "")
    if not name:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            encoding="gbk", errors="replace",
        )
        return name.lower() in (out.stdout or "").lower()
    except Exception:
        return False


def _save_script(software_id: str, action: str, text: str, ext: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"{software_id}_{action}{ext}"
    p.write_text(text, encoding="utf-8")
    return p


# ============================================================
# Tier 1 · 官方脚本接口执行
# ============================================================

def _run_official(software_id: str, action: str, **kw) -> ControlResult | None:
    """尝试走官方接口实际执行。返回 None 表示该软件没有可用的官方外部通道。"""
    bridge = registry.get_bridge(software_id)
    gen = bridge.control(action, **kw)
    if not gen.success:
        return gen

    install = _find_install(software_id)
    data = dict(gen.data or {})

    if software_id == "aftereffects":
        jsx_path = _save_script("aftereffects", action, data.get("jsx", ""), ".jsx")
        exe = install / "AfterFX.exe" if install else None
        if exe and exe.exists():
            try:
                subprocess.Popen([str(exe), "-r", str(jsx_path)])
                return ControlResult(
                    action=action, success=True, executed=True,
                    detail=f"已通过官方命令行执行: {exe.name} -r {jsx_path.name}",
                    data={**data, "script": str(jsx_path), "channel": "official-cli"},
                )
            except OSError as exc:
                return ControlResult(
                    action=action, success=False,
                    detail=f"官方 CLI 调起失败: {exc}",
                    data={**data, "script": str(jsx_path), "channel": "official-cli"},
                )
        return ControlResult(
            action=action, success=True, executed=False,
            detail=f"AE 未安装，脚本已生成待手动执行: {jsx_path}",
            data={**data, "script": str(jsx_path), "channel": "official-script"},
        )

    if software_id == "davinci":
        dll = (install / "fusionscript.dll") if install else None
        env_api = os.environ.get("RESOLVE_SCRIPT_API")
        if env_api:
            cand = Path(env_api) / "Modules" / "fusionscript.dll"
            if cand.exists():
                dll = cand
        if dll and dll.exists():
            try:
                ctypes.CDLL(str(dll))  # 加载官方脚本 API
                return ControlResult(
                    action=action, success=True, executed=False,
                    detail="fusionscript.dll 已加载；生成的脚本交给达芬奇控制台执行"
                           "（外部 Python 需与达芬奇同位数）",
                    data={**data, "dll": str(dll), "channel": "official-api"},
                )
            except OSError as exc:
                return ControlResult(
                    action=action, success=False,
                    detail=f"官方 API 加载失败: {exc}",
                    data={**data, "channel": "official-api"},
                )
        script_path = _save_script("davinci", action, data.get("script", ""), ".py")
        return ControlResult(
            action=action, success=True, executed=False,
            detail=f"未找到官方 API（fusionscript.dll），脚本已生成: {script_path}",
            data={**data, "script": str(script_path), "channel": "official-script"},
        )

    if software_id == "premiere":
        jsx_path = _save_script("premiere", action, data.get("jsx", ""), ".jsx")
        running = _is_running("premiere")
        return ControlResult(
            action=action, success=True, executed=False,
            detail=(f"PR 正在运行，请通过 文件>脚本 或 CEP 面板执行: {jsx_path}"
                    if running else
                    f"PR 未运行或无官方外部 CLI，脚本已生成待执行: {jsx_path}"),
            data={**data, "script": str(jsx_path), "channel": "official-script",
                  "running": running},
        )

    if software_id == "vegas":
        cs_path = _save_script("vegas", action, gen.detail or "", ".cs")
        return ControlResult(
            action=action, success=True, executed=False,
            detail=f"Vegas 官方脚本引擎需在软件内运行（Tools>Scripting），脚本已生成: {cs_path}",
            data={**data, "script": str(cs_path), "channel": "official-script"},
        )

    return None  # 无官方通道 → 走 UI 自动化兜底


# ============================================================
# Tier 2 · UI 自动化兜底
# ============================================================

def _run_ui_automation(software_id: str, action: str, **kw) -> ControlResult:
    bridge = registry.get_bridge(software_id)
    name = bridge.software_name
    det = bridge.detect()

    if not det.installed:
        return ControlResult(
            action=action, success=False, executed=False,
            detail=f"{name} 未安装，UI 自动化不可用（也无官方脚本通道）",
        )
    if not det.running and not kw.get("allow_launch"):
        return ControlResult(
            action=action, success=False, executed=False,
            detail=f"{name} 未运行；先启动软件后重试（或加 allow_launch=True）",
        )

    keys = kw.get("keys") or _UI_KEYMAP.get(action)
    if not keys:
        return ControlResult(
            action=action, success=False,
            detail=f"action '{action}' 无 UI 快捷键映射（可传 keys='ctrl+b,...' 自定义）",
        )

    steps = ",".join(k.strip() for k in keys.split(","))
    cmd = [
        sys.executable, str(ROOT / "scripts" / "ui_bridge.py"),
        "run", "--app", name, "--steps", steps,
    ]
    if kw.get("pid"):
        cmd += ["--pid", str(kw["pid"])]
    if kw.get("dry_run"):
        cmd += ["--dry-run"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                             encoding="utf-8", errors="replace")
        ok = out.returncode == 0
        return ControlResult(
            action=action, success=ok, executed=not kw.get("dry_run", False),
            detail=(out.stdout or out.stderr or "").strip()[-500:] or "UI 自动化已执行",
            data={"channel": "ui-automation", "keys": steps},
        )
    except subprocess.TimeoutExpired:
        return ControlResult(action=action, success=False,
                             detail="UI 自动化执行超时（60s）")


# ============================================================
# 统一入口
# ============================================================

def execute(software_id: str, action: str, prefer: str = "auto", **kw) -> ControlResult:
    """自动操作统一入口。

    prefer: auto（官方接口优先，不可用回退 UI 自动化）
            official（只用官方接口） / ui（只用 UI 自动化）
    """
    if prefer in ("auto", "official"):
        try:
            r = _run_official(software_id, action, **kw)
        except Exception as exc:  # noqa: BLE001
            if prefer == "official":
                return ControlResult(action=action, success=False,
                                     detail=f"官方通道异常: {type(exc).__name__}: {exc}")
            r = None
        if r is not None and (r.success or prefer == "official"):
            return r
    if prefer in ("auto", "ui"):
        return _run_ui_automation(software_id, action, **kw)
    return ControlResult(action=action, success=False, detail=f"未知 prefer: {prefer}")


def execute_batch(software_id: str, actions: list[str], **kw) -> list[ControlResult]:
    """批量执行：顺序执行多个 action，返回逐条结果。"""
    return [execute(software_id, a, **kw) for a in actions]
