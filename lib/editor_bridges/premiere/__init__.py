"""Adobe Premiere Pro 适配器（格式层）。

检测方式：安装路径扫描（C:\\Program Files\\Adobe\\Adobe Premiere Pro *）
          + HKLM 卸载注册表 + 进程名（Adobe Premiere Pro.exe）。
读写方式：.prproj 文件本质是 gzip 压缩的 XML。
          write 生成最小可用工程 XML 后 gzip 压缩；read 解压后用
          xml.etree.ElementTree 解析，提取 Sequence/Track/Clip 信息 → 统一 IR。
操作方式：control(action) 生成 ExtendScript(.jsx) 脚本字符串，不实际执行
          （本机未安装 PR，仅做格式层桥接）。

时间单位说明：
  PR 工程内部使用 tick（时间基）。这里取常用换算常量
  TICKS_PER_SECOND = 254016000000，统一 IR 始终使用秒（float），
  所有 read 输出秒、write 输入秒。
"""
from __future__ import annotations

import gzip
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from editor_bridges.base import (
    EditorBridge, EditorProject, EditorTrack, EditorSegment,
    DetectionResult, ControlResult, ValidationResult,
)

SOFTWARE_ID = "premiere"
SOFTWARE_NAME = "Adobe Premiere Pro"
DETECTION_METHOD = "注册表+安装路径扫描+进程名(Adobe Premiere Pro.exe)"
PROJECT_EXT = ".prproj"

# PR 时间基：每秒 tick 数（常用近似值）
TICKS_PER_SECOND = 254016000000.0

# 常见安装根目录
_INSTALL_GLOBS = [
    Path("C:/Program Files/Adobe"),
    Path("C:/Program Files (x86)/Adobe"),
]
_PROCESS_NAME = "Adobe Premiere Pro.exe"
# 注册表卸载项中用于识别 PR 的关键字
_REG_KEYWORDS = ["Adobe Premiere Pro"]


def _sec_to_ticks(sec: float) -> int:
    """秒 → PR tick。"""
    return int(round(float(sec) * TICKS_PER_SECOND))


def _ticks_to_sec(tick: float) -> float:
    """PR tick → 秒。"""
    return float(tick) / TICKS_PER_SECOND


def _scan_install() -> tuple[Path | None, str | None]:
    """扫描常见安装路径，返回 (安装目录, 版本号)。"""
    for root in _INSTALL_GLOBS:
        if not root.exists():
            continue
        for child in sorted(root.glob("Adobe Premiere Pro*")):
            if child.is_dir() and (child / _PROCESS_NAME).exists():
                # 版本号取目录名后缀，如 "Adobe Premiere Pro 2024" → "2024"
                ver = child.name.replace("Adobe Premiere Pro", "").strip() or None
                return child, ver
    return None, None


def _scan_registry() -> tuple[Path | None, str | None]:
    """扫描 HKLM 卸载注册表，查找 Premiere Pro 安装记录。"""
    try:
        import winreg
    except ImportError:
        return None, None
    hive = winreg.HKEY_LOCAL_MACHINE
    subkeys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for sub in subkeys:
        try:
            key = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    child_name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    ckey = winreg.OpenKey(key, child_name)
                except OSError:
                    continue
                try:
                    try:
                        disp_name, _ = winreg.QueryValueEx(ckey, "DisplayName")
                    except OSError:
                        continue
                    if any(kw in str(disp_name) for kw in _REG_KEYWORDS):
                        install_loc = None
                        try:
                            install_loc, _ = winreg.QueryValueEx(ckey, "InstallLocation")
                        except OSError:
                            pass
                        ver = None
                        try:
                            ver, _ = winreg.QueryValueEx(ckey, "DisplayVersion")
                        except OSError:
                            pass
                        if install_loc and Path(install_loc).exists():
                            return Path(install_loc), ver
                finally:
                    winreg.CloseKey(ckey)
        finally:
            winreg.CloseKey(key)
    return None, None


def _scan_process() -> bool:
    """检测 PR 进程是否在运行。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_PROCESS_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        return _PROCESS_NAME.lower() in out.stdout.lower()
    except Exception:
        return False


class PremiereProBridge(EditorBridge):
    software_id = SOFTWARE_ID
    software_name = SOFTWARE_NAME
    detection_method = DETECTION_METHOD
    project_ext = PROJECT_EXT

    # ---- 检测 ----

    def detect(self) -> DetectionResult:
        install_path, ver_install = _scan_install()
        reg_path, reg_ver = _scan_registry()
        if install_path is None and reg_path is not None:
            install_path, ver_install = reg_path, reg_ver
        running = _scan_process()
        installed = install_path is not None
        version = ver_install or reg_ver
        detail_parts = []
        if install_path:
            detail_parts.append(f"安装目录: {install_path}")
        if running:
            detail_parts.append("进程运行中")
        if not installed:
            detail_parts.append("未在常见路径/注册表中发现安装（格式层仍可读写 .prproj）")
        return DetectionResult(
            software_id=SOFTWARE_ID,
            software_name=SOFTWARE_NAME,
            installed=installed,
            running=running,
            version=version,
            install_path=str(install_path) if install_path else None,
            detection_method=DETECTION_METHOD,
            detail="; ".join(detail_parts),
            # 格式层支持离线读写工程文件，因此即使未安装也标记 available
            available=True,
        )

    # ---- 读：.prproj(gzip+XML) → 统一 IR ----

    def read(self, project_path: str | Path) -> EditorProject:
        p = Path(project_path)
        if not p.exists():
            raise FileNotFoundError(f"工程文件不存在: {p}")
        # 解压 gzip
        try:
            with gzip.open(p, "rb") as f:
                xml_bytes = f.read()
        except OSError as exc:
            raise ValueError(f"不是合法的 gzip 文件: {exc}") from exc
        # 解析 XML
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise ValueError(f"XML 解析失败: {exc}") from exc
        if root.tag != "Project":
            raise ValueError(f"根标签不是 <Project>，而是 <{root.tag}>")

        # 找第一个 Sequence
        seq = root.find(".//Sequence")
        if seq is None:
            raise ValueError("工程中未找到 <Sequence>")

        seq_name = (seq.findtext("Name") or p.stem).strip()
        fps = float(seq.findtext("FrameRate") or 30.0)
        duration_ticks = float(seq.findtext("Duration") or 0)
        duration_sec = _ticks_to_sec(duration_ticks)

        width = int(root.findtext("Width") or 1920)
        height = int(root.findtext("Height") or 1080)

        tracks: list[EditorTrack] = []

        # 视频轨道
        for ti, track_el in enumerate(seq.findall("./VideoTracks/Track")):
            segments = self._parse_track_segments(track_el, "video")
            tracks.append(EditorTrack(id=track_el.get("Id", f"V{ti+1}"), type="video", segments=segments))

        # 音频轨道
        for ti, track_el in enumerate(seq.findall("./AudioTracks/Track")):
            segments = self._parse_track_segments(track_el, "audio")
            tracks.append(EditorTrack(id=track_el.get("Id", f"A{ti+1}"), type="audio", segments=segments))

        # 兜底：若没有 VideoTracks/AudioTracks 分组，直接找 Track
        if not tracks:
            for ti, track_el in enumerate(seq.findall("./Track")):
                ttype = track_el.get("Type", "video")
                segments = self._parse_track_segments(track_el, ttype)
                tracks.append(EditorTrack(id=track_el.get("Id", f"T{ti+1}"), type=ttype, segments=segments))

        # 若仍无轨道，时长取片段最大结束点
        if duration_sec <= 0 and tracks:
            ends = [s.end for t in tracks for s in t.segments]
            duration_sec = max(ends) if ends else 0.0

        return EditorProject(
            name=seq_name,
            tracks=tracks,
            duration=round(duration_sec, 6),
            fps=fps,
            width=width,
            height=height,
            source_file=str(p),
            extra={"format": "prproj(gzip+xml)", "ticks_per_second": TICKS_PER_SECOND},
        )

    def _parse_track_segments(self, track_el: ET.Element, ttype: str) -> list[EditorSegment]:
        """从 <Track> 解析其下所有 <ClipItem> 为片段。"""
        segments: list[EditorSegment] = []
        for ci in track_el.findall("./ClipItem"):
            name = (ci.findtext("Name") or ci.findtext("Media/Name") or "").strip()
            start_t = float(ci.findtext("Start") or 0)
            dur_t = float(ci.findtext("Duration") or 0)
            in_t = ci.findtext("InPoint")
            out_t = ci.findtext("OutPoint")
            start_sec = _ticks_to_sec(start_t)
            dur_sec = _ticks_to_sec(dur_t)
            seg_id = ci.get("Id") or f"{name or 'clip'}_{len(segments)+1}"
            src_in = _ticks_to_sec(float(in_t)) if in_t is not None else None
            src_len = _ticks_to_sec(float(out_t) - float(in_t)) if (in_t is not None and out_t is not None) else dur_sec
            segments.append(EditorSegment(
                id=seg_id,
                track_type=ttype,
                t=round(start_sec, 6),
                length=round(dur_sec, 6),
                material=name or None,
                src_in=round(src_in, 6) if src_in is not None else None,
                src_len=round(src_len, 6) if src_len is not None else None,
            ))
        return segments

    # ---- 写：统一 IR → .prproj(gzip+XML) ----

    def write(self, project: EditorProject, output_path: str | Path) -> Path:
        p = Path(output_path)
        if p.suffix.lower() != PROJECT_EXT:
            p = p / f"project{PROJECT_EXT}"
        p.parent.mkdir(parents=True, exist_ok=True)

        # 构造根 <Project>
        root = ET.Element("Project", {"Version": "1", "Scheme": "aicut-prproj"})
        ET.SubElement(root, "Name").text = project.name
        ET.SubElement(root, "Width").text = str(project.width)
        ET.SubElement(root, "Height").text = str(project.height)

        sequences_el = ET.SubElement(root, "Sequences")
        seq_el = ET.SubElement(sequences_el, "Sequence")
        ET.SubElement(seq_el, "Name").text = project.name or "Sequence.01"
        ET.SubElement(seq_el, "FrameRate").text = str(project.fps)
        ET.SubElement(seq_el, "Duration").text = str(_sec_to_ticks(project.duration))

        video_tracks_el = ET.SubElement(seq_el, "VideoTracks")
        audio_tracks_el = ET.SubElement(seq_el, "AudioTracks")

        v_idx = 0
        a_idx = 0
        for tr in project.tracks:
            is_audio = tr.type in ("audio",)
            track_container = audio_tracks_el if is_audio else video_tracks_el
            track_id = f"{tr.type[0].upper()}{(a_idx if is_audio else v_idx)+1}"
            track_el = ET.SubElement(track_container, "Track", {"Type": tr.type, "Id": track_id})
            for seg in tr.segments:
                self._build_clip_item(track_el, seg)
            if is_audio:
                a_idx += 1
            else:
                v_idx += 1

        # 若工程没有任何轨道，至少放一条空视频轨道，保证结构合法
        if v_idx == 0 and a_idx == 0:
            ET.SubElement(video_tracks_el, "Track", {"Type": "video", "Id": "V1"})

        # 序列化 XML
        ET.indent(root, space="  ")
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)

        # gzip 压缩写出
        with gzip.open(p, "wb", compresslevel=9) as f:
            f.write(xml_bytes)
        return p

    def _build_clip_item(self, track_el: ET.Element, seg: EditorSegment) -> None:
        """把一个统一 IR 片段写成 <ClipItem>。"""
        ci = ET.SubElement(track_el, "ClipItem", {"Id": seg.id})
        ET.SubElement(ci, "Name").text = seg.material or seg.text or seg.id
        ET.SubElement(ci, "Start").text = str(_sec_to_ticks(seg.t))
        ET.SubElement(ci, "Duration").text = str(_sec_to_ticks(seg.length))
        src_in = seg.src_in if seg.src_in is not None else 0.0
        src_len = seg.src_len if seg.src_len is not None else seg.length
        ET.SubElement(ci, "InPoint").text = str(_sec_to_ticks(src_in))
        ET.SubElement(ci, "OutPoint").text = str(_sec_to_ticks(src_in + src_len))
        media = ET.SubElement(ci, "Media")
        ET.SubElement(media, "Name").text = seg.material or seg.text or seg.id
        ET.SubElement(media, "Type").text = "Audio" if seg.track_type == "audio" else "Video"
        if seg.speed != 1.0:
            ET.SubElement(ci, "Speed").text = str(seg.speed)

    # ---- 控制：生成 ExtendScript(.jsx)，不实际执行 ----

    def control(self, action: str, **kwargs) -> ControlResult:
        """生成在 PR 中执行的 ExtendScript(.jsx) 脚本字符串。

        支持 action: cut / delete / play / undo / open / save。
        本机未安装 PR，此处仅生成脚本内容，不执行。
        """
        # 各操作对应的 PR ExtendScript 片段
        script_map: dict[str, str] = {
            "cut": (
                "// 剪切当前时间指针处的片段\n"
                "var proj = app.project;\n"
                "var seq = proj.activeSequence;\n"
                "if (seq) {\n"
                "    app.executeCommand(40051); // Split（时间线分割）\n"
                "}\n"
            ),
            "delete": (
                "// 删除选中片段\n"
                "app.executeCommand(101)\n"
            ),
            "play": (
                "// 播放/暂停\n"
                "app.executeCommand(103)\n"
            ),
            "undo": (
                "// 撤销\n"
                "app.executeCommand(2003)\n"
            ),
            "open": (
                "// 打开工程\n"
                "var path = '%s';\n"
                "app.openDocument(new File(path));\n" % kwargs.get("project_path", "<project.prproj>")
            ),
            "save": (
                "// 保存工程\n"
                "app.project.save();\n"
            ),
        }
        if action not in script_map:
            return ControlResult(
                action=action, success=False,
                detail=f"不支持的操作: {action}（可选: {', '.join(script_map)}）",
            )
        jsx = (
            "// Adobe Premiere Pro ExtendScript 桥接脚本\n"
            "// 由 AI cut 自动生成，在 PR 中通过 文件>脚本 运行\n"
            "app.beginSuppressDialogs();\n"
            "try {\n"
            + "\n".join("    " + ln for ln in script_map[action].splitlines())
            + "\n} catch (e) { alert('PR 脚本错误: ' + e); }\n"
            "app.endSuppressDialogs(false);\n"
        )
        return ControlResult(
            action=action, success=True,
            detail=f"已生成 {action} 操作的 ExtendScript（未实际执行，需在 PR 中运行）",
            data={"jsx": jsx, "format": "jsx", "executed": False},
        )

    # ---- 校验 ----

    def validate(self, project_path: str | Path) -> ValidationResult:
        """校验 .prproj：必须是合法 gzip，解压后 XML 根标签为 Project 且含 Sequence。"""
        p = Path(project_path)
        if not p.exists():
            return ValidationResult(valid=False, errors=[f"文件不存在: {p}"])
        errors: list[str] = []
        warnings: list[str] = []
        info: dict[str, Any] = {}
        # 1. gzip 合法性
        try:
            with gzip.open(p, "rb") as f:
                xml_bytes = f.read()
        except OSError as exc:
            return ValidationResult(valid=False, errors=[f"gzip 解压失败: {exc}"])
        # 2. XML 合法性
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            return ValidationResult(valid=False, errors=[f"XML 解析失败: {exc}"])
        info["root_tag"] = root.tag
        if root.tag != "Project":
            errors.append(f"根标签应为 <Project>，实际为 <{root.tag}>")
        seq = root.find(".//Sequence")
        if seq is None:
            errors.append("缺少 <Sequence> 节点")
        else:
            clip_count = len(root.findall(".//ClipItem"))
            track_count = len(root.findall(".//Track"))
            info["sequence"] = (seq.findtext("Name") or "").strip()
            info["track_count"] = track_count
            info["clip_count"] = clip_count
            if clip_count == 0:
                warnings.append("工程无片段（ClipItem）")
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            info=info,
        )


BRIDGE_CLASS = PremiereProBridge
