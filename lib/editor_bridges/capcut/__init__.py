r"""CapCut 适配器。

CapCut（国际版剪映）与剪映专业版共用草稿引擎（draft_content.json 结构相同），
但进程名、窗口标题、安装路径不同。

检测方式：窗口标题 + 进程名（CapCut.exe）+ 草稿目录扫描。
读写方式：与剪映相同的明文草稿 JSON。
操作方式：窗口激活 + SendInput 快捷键。
"""
from __future__ import annotations
from aicut.portability import portable_path

import os
import subprocess
from pathlib import Path

from editor_bridges.base import DetectionResult
from editor_bridges.jianying import JianYingBridge

SOFTWARE_ID = "capcut"
SOFTWARE_NAME = "CapCut"
DETECTION_METHOD = "窗口标题+进程名(CapCut.exe)+草稿目录扫描"
PROJECT_EXT = ".json"

_INSTALL_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut",
    Path(portable_path('user:AppData/Local/CapCut')),
    Path("C:/Program Files/CapCut"),
]


def _scan_processes() -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        for line in out.stdout.strip().splitlines():
            if "CapCut" in line:
                found.append({"process": "CapCut.exe", "detail": line.strip()})
    except Exception:
        pass
    return found


def _find_install() -> Path | None:
    for cand in _INSTALL_CANDIDATES:
        if cand.exists():
            return cand
    return None


class CapCutBridge(JianYingBridge):
    """CapCut 复用剪映的读写引擎，仅覆盖检测与操作相关配置。"""
    software_id = SOFTWARE_ID
    software_name = SOFTWARE_NAME
    detection_method = DETECTION_METHOD
    project_ext = PROJECT_EXT

    def detect(self) -> DetectionResult:
        install_path = _find_install()
        processes = _scan_processes()
        running = len(processes) > 0
        installed = install_path is not None
        version = None
        if install_path:
            ver_file = install_path / "version" / "version"
            if ver_file.exists():
                try:
                    version = ver_file.read_text(encoding="utf-8-sig").strip()
                except Exception:
                    pass
        detail_parts = []
        if install_path:
            detail_parts.append(f"安装: {install_path}")
        if processes:
            detail_parts.append(f"运行中: {len(processes)} 个进程")
        # CapCut 草稿目录通常在用户目录下
        capcut_drafts = Path(os.environ.get("APPDATA", "")) / "CapCut" / "User Data" / "Projects"
        draft_count = 0
        if capcut_drafts.exists():
            draft_count = len([d for d in capcut_drafts.iterdir() if d.is_dir()])
        if draft_count:
            detail_parts.append(f"工程: {draft_count} 个")
        return DetectionResult(
            software_id=SOFTWARE_ID,
            software_name=SOFTWARE_NAME,
            installed=installed,
            running=running,
            version=version,
            install_path=str(install_path) if install_path else None,
            project_path=str(capcut_drafts) if capcut_drafts.exists() else None,
            detection_method=DETECTION_METHOD,
            detail="; ".join(detail_parts),
            available=installed,
        )

    def control(self, action: str, **kwargs):
        """CapCut 窗口操作，使用 capcut 程序键激活窗口。"""
        # 复用父类逻辑，但窗口识别用 capcut
        try:
            import sys
            skill_path = portable_path('user:AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/.user_skills/ai-auto-editor/scripts')
            if skill_path not in sys.path:
                sys.path.insert(0, skill_path)
            from aieditor.app_control import activate_editor, send_hotkey
            from aieditor.editor_programs import parse_hotkey
            from editor_bridges.base import ControlResult

            hotkeys = {
                "cut": "ctrl+b", "delete": "delete", "play": "space",
                "undo": "ctrl+z", "mark_in": "i", "mark_out": "o",
                "ripple_delete": "shift+delete",
            }
            if action not in hotkeys:
                return ControlResult(action=action, success=False,
                                     detail=f"不支持的操作: {action}")
            hwnd = activate_editor("capcut")
            if not hwnd:
                return ControlResult(action=action, success=False, detail="未找到 CapCut 窗口")
            vks = parse_hotkey(hotkeys[action])
            send_hotkey(vks)
            return ControlResult(action=action, success=True,
                                 detail=f"已发送 {hotkeys[action]} 到 CapCut 窗口")
        except Exception as exc:
            from editor_bridges.base import ControlResult
            return ControlResult(action=action, success=False, detail=f"操作失败: {exc}")


BRIDGE_CLASS = CapCutBridge
