"""Final Cut Pro 适配器。

检测方式：FCPXML 文件扩展名检测（Mac 专属，Windows 仅格式层）。
读写方式：Apple 公开的 FCPXML（标准 XML，有官方 DTD）解析/生成。
操作方式：FCP 为 Mac 专属软件，Windows 无窗口可操作，control 一律返回失败提示。

FCPXML 核心结构：
    fcpxml > library > event > project > sequence > spine > (video|audio|title)
其中 spine 是主时间线，video/audio/title 直接挂在 spine 下，
本适配器把它们映射为统一 IR 的 video / audio / text 三类轨道。

时间格式两种：
    1) "Ns"        整数秒，如 "5s"
    2) "N/Ms"      分数秒，如 "100/3000s" 表示 100/3000 秒（≈1/30s）
"""
from __future__ import annotations
from aicut.portability import portable_path

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from editor_bridges.base import (
    EditorBridge, EditorProject, EditorTrack, EditorSegment,
    DetectionResult, ControlResult, ValidationResult,
)

SOFTWARE_ID = "finalcut"
SOFTWARE_NAME = "Final Cut Pro"
DETECTION_METHOD = "FCPXML文件扩展名检测(Mac专属,Windows仅格式层)"
PROJECT_EXT = ".fcpxml"

# FCPXML 版本号（Apple 公开 DTD 当前常用 1.10）
FCPXML_VERSION = "1.10"

# 片段元素标签 → 统一 IR 轨道类型
_TAG_TO_TRACK_TYPE = {
    "video": "video",
    "audio": "audio",
    "title": "text",
}
_TRACK_TYPE_TO_TAG = {v: k for k, v in _TAG_TO_TRACK_TYPE.items()}


# ============================================================
# 时间解析 / 格式化
# ============================================================

def _parse_time(value: str | None) -> float:
    """把 FCPXML 时间字符串解析为 float 秒。

    支持两种格式：
      - "5s"      → 5.0
      - "100/3000s" → 100/3000 ≈ 0.0333
      - "0s"      → 0.0
    非法或空值返回 0.0。
    """
    if value is None:
        return 0.0
    s = str(value).strip()
    if s.endswith("s"):
        s = s[:-1]
    if not s:
        return 0.0
    # 分数格式
    if "/" in s:
        num, _, den = s.partition("/")
        try:
            den_f = float(den)
            if den_f == 0:
                return 0.0
            return float(num) / den_f
        except (ValueError, ZeroDivisionError):
            return 0.0
    # 普通数字
    try:
        return float(s)
    except ValueError:
        return 0.0


def _format_time(seconds: float, fps: float = 30.0) -> str:
    """把 float 秒格式化为 FCPXML 时间字符串。

    整数秒用 "Ns"；非整数用分数 "N/Ms"，分母取 fps*1000 保证精度。
    """
    if seconds is None:
        return "0s"
    # 判断是否为整数秒（容差很小）
    rounded = round(seconds)
    if abs(seconds - rounded) < 1e-6:
        return f"{int(rounded)}s"
    # 分数格式：分母 = fps*1000
    denom = int(round(fps * 1000))
    if denom <= 0:
        denom = 30000
    num = int(round(seconds * denom))
    return f"{num}/{denom}s"


# ============================================================
# 适配器主类
# ============================================================

class FinalCutBridge(EditorBridge):
    software_id = SOFTWARE_ID
    software_name = SOFTWARE_NAME
    detection_method = DETECTION_METHOD
    project_ext = PROJECT_EXT

    # ---- 检测 ----

    def detect(self) -> DetectionResult:
        """检测 FCP 安装状态。

        FCP 是 Mac 专属（.app bundle）：
          - 仅在 darwin（macOS）上才可能真正安装；
          - Windows 上永远 installed=False，但 available=True（支持离线 FCPXML 读写）。
        同时扫描项目目录下是否存在 .fcpxml 文件。
        """
        is_mac = sys.platform == "darwin"
        installed = False
        running = False
        install_path: str | None = None
        version: str | None = None

        if is_mac:
            # macOS 上检查 /Applications/Final Cut Pro.app
            app = Path("/Applications/Final Cut Pro.app")
            if app.exists():
                installed = True
                install_path = str(app)
                # 尝试读版本
                info_plist = app / "Contents" / "Info.plist"
                if info_plist.exists():
                    try:
                        tree = ET.parse(str(info_plist))
                        # 简易提取 CFBundleShortVersionString
                        for el in tree.iter():
                            if el.tag.endswith("key") and (el.text or "").strip() == "CFBundleShortVersionString":
                                nxt = el.findnext()  # type: ignore[attr-defined]
                                if nxt is not None and nxt.text:
                                    version = nxt.text.strip()
                                break
                    except Exception:
                        pass

        # 扫描项目目录下是否有 FCPXML 文件
        proj_root = Path(portable_path('project:'))
        found_files: list[Path] = []
        if proj_root.is_dir():
            try:
                found_files = sorted(
                    f for f in proj_root.rglob("*.fcpxml")
                    if f.is_file()
                )
            except Exception:
                found_files = []

        detail = "Mac 专属，Windows 仅提供 FCPXML 格式层适配。"
        if is_mac:
            detail = "macOS 平台，可检测 Final Cut Pro.app。"
        if found_files:
            detail += f" 发现 {len(found_files)} 个 FCPXML 文件。"

        return DetectionResult(
            software_id=SOFTWARE_ID,
            software_name=SOFTWARE_NAME,
            installed=installed,
            running=running,
            version=version,
            install_path=install_path,
            project_path=str(found_files[0]) if found_files else None,
            detection_method=DETECTION_METHOD,
            detail=detail,
            # Windows 上 installed=False，但因支持离线 FCPXML 读写，available=True
            available=True,
        )

    # ---- 读取 ----

    def read(self, project_path: str | Path) -> EditorProject:
        """解析 .fcpxml → 统一 IR。"""
        p = Path(project_path)
        if not p.exists():
            raise FileNotFoundError(f"FCPXML 文件不存在: {p}")

        tree = ET.parse(str(p))
        root = tree.getroot()
        if root.tag != "fcpxml":
            raise ValueError(f"根标签不是 fcpxml，而是 <{root.tag}>")

        # 查找 spine（主时间线）
        spine = root.find(".//spine")
        if spine is None:
            raise ValueError("FCPXML 中缺少 <spine> 结构")

        # 尝试从 sequence format 推导 fps / 分辨率
        sequence = root.find(".//sequence")
        fps = 30.0
        width, height = 1920, 1080
        if sequence is not None:
            fmt = sequence.get("format", "")
            # 形如 FFVideoFormat1080p30 → 提取分辨率与帧率
            if "1080p30" in fmt:
                width, height, fps = 1920, 1080, 30.0
            elif "4k" in fmt.lower() or "2160p" in fmt:
                width, height = 3840, 2160

        # project 名
        project_el = root.find(".//project")
        proj_name = project_el.get("name", p.stem) if project_el is not None else p.stem

        # 按轨道类型分组收集片段
        # track_type → segments
        tracks_map: dict[str, list[EditorSegment]] = {"video": [], "audio": [], "text": []}
        seg_counter = 0
        max_end = 0.0

        for child in list(spine):
            tag = child.tag
            track_type = _TAG_TO_TRACK_TYPE.get(tag)
            if track_type is None:
                continue  # 忽略 spine 下其他元素（如 gap、ref 等）

            offset = _parse_time(child.get("offset"))
            duration = _parse_time(child.get("duration"))
            start = _parse_time(child.get("start"))
            name = child.get("name", "")

            text_content: str | None = None
            if track_type == "text":
                # title 元素下的 <text> 标签提取字幕文本
                text_el = child.find("text")
                if text_el is not None:
                    text_content = (text_el.text or "").strip() or None

            seg_counter += 1
            seg = EditorSegment(
                id=f"seg_{seg_counter:03d}",
                track_type=track_type,
                t=offset,
                length=duration,
                material=name or None,
                text=text_content,
                src_in=start if start > 0 else None,
                src_len=duration if duration > 0 else None,
            )
            tracks_map[track_type].append(seg)
            max_end = max(max_end, offset + duration)

        # 构建 EditorTrack 列表（只保留有片段的轨道）
        tracks: list[EditorTrack] = []
        track_idx = 0
        for ttype in ("video", "audio", "text"):
            segs = tracks_map[ttype]
            if not segs:
                continue
            track_idx += 1
            tracks.append(EditorTrack(
                id=f"trk_{track_idx:03d}",
                type=ttype,
                segments=segs,
            ))

        return EditorProject(
            name=proj_name,
            tracks=tracks,
            duration=round(max_end, 6),
            fps=fps,
            width=width,
            height=height,
            source_file=str(p),
            extra={"fcpxml_version": root.get("version", FCPXML_VERSION)},
        )

    # ---- 写入 ----

    def write(self, project: EditorProject, output_path: str | Path) -> Path:
        """统一 IR → 生成合法的 .fcpxml 文件。"""
        p = Path(output_path)
        if p.suffix.lower() != ".fcpxml":
            p = p / f"{project.name}.fcpxml"
        p.parent.mkdir(parents=True, exist_ok=True)

        # 决定 format 字符串
        if project.width == 3840 or project.height >= 2160:
            fmt = "FFVideoFormat3840x2160p30"
        else:
            fmt = "FFVideoFormat1080p30"

        # 构建 XML 树
        root = ET.Element("fcpxml", {"version": FCPXML_VERSION})
        library = ET.SubElement(root, "library")
        event = ET.SubElement(library, "event", {"name": "AI Cut Event"})
        project_el = ET.SubElement(event, "project", {"name": project.name})
        sequence = ET.SubElement(project_el, "sequence", {
            "format": fmt,
            "duration": _format_time(project.duration or 5.0, project.fps),
            "tcStart": "0s",
            "tcFormat": "NDF",
        })
        spine = ET.SubElement(sequence, "spine")

        # 把所有片段按轨道类型写入 spine
        for seg in project.all_segments:
            tag = _TRACK_TYPE_TO_TAG.get(seg.track_type, "video")
            attrs: dict[str, str] = {
                "offset": _format_time(seg.t, project.fps),
                "duration": _format_time(seg.length, project.fps),
            }
            if seg.material:
                attrs["name"] = seg.material
            if seg.src_in is not None:
                attrs["start"] = _format_time(seg.src_in, project.fps)

            clip_el = ET.SubElement(spine, tag, attrs)

            # title 元素内嵌 <text> 字幕文本
            if seg.track_type == "text" and seg.text:
                text_el = ET.SubElement(clip_el, "text")
                text_el.text = seg.text

            # audio 片段补一个 role 子元素（符合 FCPXML 惯例）
            if tag == "audio":
                ET.SubElement(clip_el, "audio", {"role": "dialogue"}).text = None

        # 写出 XML（声明 UTF-8，保证中文正常）
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")  # 美化缩进
        with open(str(p), "wb") as f:
            f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            tree.write(f, encoding="utf-8", xml_declaration=False)

        return p

    # ---- 控制 ----

    def control(self, action: str, **kwargs) -> ControlResult:
        """FCP 为 Mac 专属，Windows 不支持任何窗口操作。"""
        return ControlResult(
            action=action,
            success=False,
            detail="FCP 为 Mac 专属，Windows 不支持窗口操作。请在 Mac 上通过 FCPXML 导入工程后手动操作。",
        )

    # ---- 校验 ----

    def validate(self, project_path: str | Path) -> ValidationResult:
        """校验 FCPXML 文件合法性。"""
        p = Path(project_path)
        if not p.exists():
            return ValidationResult(valid=False, errors=[f"文件不存在: {p}"])

        errors: list[str] = []
        warnings: list[str] = []
        info: dict[str, Any] = {}

        # 1) XML 可解析
        try:
            tree = ET.parse(str(p))
        except ET.ParseError as exc:
            return ValidationResult(valid=False, errors=[f"XML 解析失败: {exc}"])

        root = tree.getroot()

        # 2) 根标签是 fcpxml
        if root.tag != "fcpxml":
            errors.append(f"根标签不是 fcpxml，而是 <{root.tag}>")

        # 3) 有 version 属性
        version = root.get("version")
        if not version:
            errors.append("fcpxml 根元素缺少 version 属性")
        else:
            info["fcpxml_version"] = version

        # 4) 包含 library/event/project/sequence/spine 结构
        for tag in ("library", "event", "project", "sequence", "spine"):
            if root.find(f".//{tag}") is None:
                errors.append(f"FCPXML 缺少 <{tag}> 结构")

        # 5) 时间格式合法性检查（抽查 spine 下片段的时间属性）
        spine = root.find(".//spine")
        seg_count = 0
        if spine is not None:
            for child in list(spine):
                if child.tag not in _TAG_TO_TRACK_TYPE:
                    continue
                seg_count += 1
                for tattr in ("offset", "duration", "start"):
                    raw = child.get(tattr)
                    if raw is None:
                        continue
                    # 尝试解析，失败即非法
                    try:
                        _parse_time(raw)
                    except Exception as exc:
                        errors.append(
                            f"片段 {child.tag} 的 {tattr}='{raw}' 时间格式非法: {exc}"
                        )

        info["segment_count"] = seg_count
        if seg_count == 0:
            warnings.append("spine 下没有 video/audio/title 片段")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            info=info,
        )


BRIDGE_CLASS = FinalCutBridge
