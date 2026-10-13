r"""DaVinci Resolve 适配器。

检测方式：官方 Python Scripting API(Resolve.exe) + 安装路径扫描 + 进程检测。
读写方式：.drp 工程导出文件（XML 格式；达芬奇「导出项目」得到的即是 XML 文本）。
操作方式：生成达芬奇 Python API 脚本（import DaVinciResolveScript as dvr），
          由达芬奇控制台或外部解释器执行。

时间单位说明：
- 统一 IR 用「秒」（float）；达芬奇 .drp XML 内部用「帧」（int）。
- 帧 → 秒 = 帧 / fps；秒 → 帧 = round(秒 * fps)。

注意：
- .drt 是达芬奇预设文件，不是完整工程，本适配器不解析其内容，仅给出说明。
- 达芬奇免费版也自带 fusionscript.dll / DaVinciResolveScript.py，支持 Python 脚本。
"""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from editor_bridges.base import (
    EditorBridge, EditorProject, EditorTrack, EditorSegment,
    DetectionResult, ControlResult, ValidationResult,
)

SOFTWARE_ID = "davinci"
SOFTWARE_NAME = "DaVinci Resolve"
DETECTION_METHOD = "官方Python Scripting API(Resolve.exe)+安装路径+进程检测"
PROJECT_EXT = ".drp"

# 达芬奇常见安装路径（64 位 / 32 位）
_INSTALL_CANDIDATES = [
    Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve"),
    Path(r"C:\Program Files (x86)\Blackmagic Design\DaVinci Resolve"),
]

# 达芬奇 Python scripting 模块常见位置
_SCRIPT_MODULE_CANDIDATES = [
    "fusionscript.dll",
    Path("Developer/Scripting/Modules/DaVinciResolveScript.py"),
]

# 进程名
_PROCESS_NAME = "Resolve.exe"


def _find_install() -> Path | None:
    """查找达芬奇安装目录。"""
    for cand in _INSTALL_CANDIDATES:
        if cand.exists() and (cand / _PROCESS_NAME).exists():
            return cand
    return None


def _has_scripting_support(install_path: Path | None) -> bool:
    """检测是否存在 Python scripting 支持（免费版也带 fusionscript.dll）。"""
    if install_path is None:
        return False
    for rel in _SCRIPT_MODULE_CANDIDATES:
        if (install_path / rel).exists():
            return True
    return False


def _scan_process() -> bool:
    """检测 Resolve.exe 是否在运行。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_PROCESS_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        return _PROCESS_NAME.lower() in out.stdout.lower()
    except Exception:
        return False


def _check_registry() -> str | None:
    """查询注册表获取达芬奇版本（读取失败不抛错）。"""
    try:
        import winreg
    except ImportError:
        return None
    for root, sub in [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Blackmagic Design\DaVinci Resolve"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Blackmagic Design\DaVinci Resolve"),
    ]:
        try:
            key = winreg.OpenKey(root, sub)
            try:
                val, _ = winreg.QueryValueEx(key, "Version")
                return str(val)
            finally:
                winreg.CloseKey(key)
        except OSError:
            continue
    return None


# ============================================================
# 帧 ↔ 秒 换算
# ============================================================

def _sec_to_frames(sec: float | None, fps: float) -> int:
    """秒 → 帧（四舍五入）。None 视为 0。"""
    if sec is None:
        return 0
    return max(0, int(round(float(sec) * fps)))


def _frames_to_sec(frames: int | str | None, fps: float) -> float:
    """帧 → 秒。"""
    if frames is None or frames == "":
        return 0.0
    try:
        f = int(frames)
    except (TypeError, ValueError):
        return 0.0
    return round(f / fps, 6)


class DaVinciBridge(EditorBridge):
    software_id = SOFTWARE_ID
    software_name = SOFTWARE_NAME
    detection_method = DETECTION_METHOD
    project_ext = PROJECT_EXT

    # ---- 检测 ----

    def detect(self) -> DetectionResult:
        install_path = _find_install()
        running = _scan_process()
        version = _check_registry()
        has_script = _has_scripting_support(install_path)
        installed = install_path is not None

        detail_parts: list[str] = []
        if install_path:
            detail_parts.append(f"安装: {install_path}")
        else:
            detail_parts.append("未发现安装目录")
        if version:
            detail_parts.append(f"版本: {version}")
        if running:
            detail_parts.append("Resolve.exe 运行中")
        if has_script:
            detail_parts.append("Python scripting 可用")
        # 格式层：即使未安装，也可离线读写 .drp XML
        detail_parts.append("离线 .drp XML 读写可用")

        return DetectionResult(
            software_id=SOFTWARE_ID,
            software_name=SOFTWARE_NAME,
            installed=installed,
            running=running,
            version=version,
            install_path=str(install_path) if install_path else None,
            detection_method=DETECTION_METHOD,
            detail="; ".join(detail_parts),
            # 格式层适配器：未安装也支持离线工程读写，故 available=True
            available=True,
        )

    # ---- 读取 .drp（XML）→ 统一 IR ----

    def read(self, project_path: str | Path) -> EditorProject:
        p = Path(project_path)
        if not p.exists():
            raise FileNotFoundError(f"工程文件不存在: {p}")
        if p.suffix.lower() == ".drt":
            raise ValueError(
                ".drt 是达芬奇预设文件（不是完整工程），无法直接解析为时间线；"
                "请在达芬奇中导入预设后导出为 .drp 再读取。"
            )
        if p.suffix.lower() != ".drp":
            # 容错：也允许读 .xml
            if p.suffix.lower() != ".xml":
                raise ValueError(f"不支持的文件类型: {p.suffix}（应为 .drp）")

        tree = ET.parse(p)
        root = tree.getroot()
        # 根标签可能是 <DaVinciResolveProject> 或 <Project>
        if root.tag not in ("DaVinciResolveProject", "Project"):
            raise ValueError(f"非法 .drp XML：根标签 <{root.tag}> 不被识别")

        # 找到 Timeline 节点（兼容 <DaVinciResolveProject><Project><Timelines><Timeline> 结构）
        timeline_el = root.find(".//Timeline")
        if timeline_el is None:
            raise ValueError("工程 XML 中未找到 <Timeline> 节点")

        fps = float(timeline_el.get("FPS", 30))
        width = int(timeline_el.get("Width", 1920))
        height = int(timeline_el.get("Height", 1080))
        proj_name = timeline_el.get("Name", p.stem)

        tracks: list[EditorTrack] = []
        max_end_frame = 0

        for track_el in timeline_el.findall("./Tracks/Track"):
            tr_type = track_el.get("Type", "video").lower()
            tr_idx = track_el.get("Index", str(len(tracks) + 1))
            segments: list[EditorSegment] = []
            for clip_el in track_el.findall("./Clips/Clip"):
                start_f = int(clip_el.get("Start", 0))
                dur_f = int(clip_el.get("Duration", 0))
                in_f = int(clip_el.get("In", 0))
                out_f = int(clip_el.get("Out", start_f + dur_f))
                src_len_f = out_f - in_f
                seg = EditorSegment(
                    id=clip_el.get("Id", f"clip_{start_f}_{len(segments)}"),
                    track_type=tr_type,
                    t=_frames_to_sec(start_f, fps),
                    length=_frames_to_sec(dur_f, fps),
                    material=clip_el.get("Name"),
                    src_in=_frames_to_sec(in_f, fps),
                    src_len=_frames_to_sec(src_len_f, fps),
                    speed=float(clip_el.get("Speed", 1.0)),
                )
                segments.append(seg)
                max_end_frame = max(max_end_frame, start_f + dur_f)
            tracks.append(EditorTrack(id=f"track_{tr_idx}", type=tr_type, segments=segments))

        return EditorProject(
            name=proj_name,
            tracks=tracks,
            duration=_frames_to_sec(max_end_frame, fps),
            fps=fps,
            width=width,
            height=height,
            source_file=str(p),
            extra={"root_tag": root.tag},
        )

    # ---- 统一 IR → .drp（XML）----

    def write(self, project: EditorProject, output_path: str | Path) -> Path:
        p = Path(output_path)
        if p.suffix.lower() != ".drp":
            p = p.parent / f"{p.name}.drp"
        p.parent.mkdir(parents=True, exist_ok=True)

        fps = project.fps or 30.0

        # 根元素
        root = ET.Element("DaVinciResolveProject", {"Version": "1.0"})
        proj_el = ET.SubElement(root, "Project", {"Name": project.name})
        timelines_el = ET.SubElement(proj_el, "Timelines")
        timeline_el = ET.SubElement(timelines_el, "Timeline", {
            "Name": project.name,
            "FPS": str(fps),
            "Width": str(project.width),
            "Height": str(project.height),
        })
        tracks_el = ET.SubElement(timeline_el, "Tracks")

        for tr_i, track in enumerate(project.tracks, start=1):
            track_el = ET.SubElement(tracks_el, "Track", {
                "Type": track.type,
                "Index": str(tr_i),
            })
            clips_el = ET.SubElement(track_el, "Clips")
            for seg_i, seg in enumerate(track.segments):
                start_f = _sec_to_frames(seg.t, fps)
                dur_f = _sec_to_frames(seg.length, fps)
                src_in_f = _sec_to_frames(seg.src_in, fps)
                # Out = In + 素材引用时长（秒→帧）；缺省用 Duration
                if seg.src_len is not None:
                    out_f = src_in_f + _sec_to_frames(seg.src_len, fps)
                else:
                    out_f = start_f + dur_f
                ET.SubElement(clips_el, "Clip", {
                    "Id": seg.id or f"clip_{tr_i}_{seg_i}",
                    "Name": seg.material or seg.text or f"clip_{seg_i}",
                    "Start": str(start_f),
                    "Duration": str(dur_f),
                    "In": str(src_in_f),
                    "Out": str(out_f),
                    "Speed": str(seg.speed),
                })

        # 写出带 XML 声明、缩进
        ET.indent(root, space="  ")
        tree = ET.ElementTree(root)
        tree.write(p, encoding="utf-8", xml_declaration=True)
        return p

    # ---- 控制：生成达芬奇 Python 脚本 ----

    def control(self, action: str, **kwargs) -> ControlResult:
        """生成达芬奇 Python Scripting API 脚本内容。

        支持 action: cut / delete / play / undo / stop / save。
        脚本需在达芬奇「控制台」或配置好 RESOLVE_SCRIPT_API 的解释器中运行。
        """
        action_map = {
            "cut": "timeline.SplitTimelineClipAtCurrentFrame()",
            "delete": "timeline.DeleteClips()",
            "play": "timeline.Play()",
            "stop": "timeline.Stop()",
            "undo": "project.Undo()",
            "save": "projectManager.SaveProject()",
        }
        if action not in action_map:
            return ControlResult(
                action=action, success=False,
                detail=f"不支持的操作: {action}（可选: {', '.join(action_map)}）",
            )

        script = f'''# DaVinci Resolve Python Scripting（由 AI cut 生成）
# 在达芬奇 控制台 或 配置好 RESOLVE_SCRIPT_API/PYTHONHOME 的 Python 中运行
import DaVinciResolveScript as dvr

resolve = dvr.scriptapp("Resolve")
projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline()

if timeline is None:
    print("错误：当前没有打开的 Timeline")
    raise SystemExit(1)

# 执行操作: {action}
result = {action_map[action]}
print("操作完成: {action} ->", result)
'''
        return ControlResult(
            action=action, success=True,
            detail=f"已生成达芬奇 Python 脚本（{action}）",
            data={"script": script, "language": "python", "api": "DaVinciResolveScript"},
        )

    # ---- 校验 ----

    def validate(self, project_path: str | Path) -> ValidationResult:
        """校验 .drp XML：根标签合法、含 Timeline/Track 节点。"""
        p = Path(project_path)
        if not p.exists():
            return ValidationResult(valid=False, errors=[f"文件不存在: {p}"])
        errors: list[str] = []
        warnings: list[str] = []
        info: dict[str, Any] = {}
        try:
            tree = ET.parse(p)
            root = tree.getroot()
        except ET.ParseError as exc:
            return ValidationResult(valid=False, errors=[f"XML 解析失败: {exc}"])

        if root.tag not in ("DaVinciResolveProject", "Project"):
            errors.append(f"根标签 <{root.tag}> 不是 <DaVinciResolveProject>/<Project>")
        timeline_el = root.find(".//Timeline")
        if timeline_el is None:
            errors.append("缺少 <Timeline> 节点")
        track_els = root.findall(".//Tracks/Track")
        if timeline_el is not None and not track_els:
            errors.append("<Timeline> 内没有 <Track> 轨道")

        # 统计片段
        clip_count = len(root.findall(".//Clips/Clip"))
        info["track_count"] = len(track_els)
        info["clip_count"] = clip_count
        if clip_count == 0:
            warnings.append("工程没有任何 Clip 片段")
        info["root_tag"] = root.tag
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            info=info,
        )


BRIDGE_CLASS = DaVinciBridge
