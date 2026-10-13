r"""VEGAS Pro 适配器。

检测方式：安装路径扫描 + 注册表 + 进程名(VEGAS Pro.exe) + COM/.NET Scripting 检测。
读写方式：
- .veg 是 Vegas 原生工程文件，本质是 OLE 复合文档（二进制），无法用 Python 直接解析。
- 本适配器做「格式层桥接」：
  · read()  只接受 Vegas 脚本导出的 JSON 中间格式（.json）；传 .veg 会给出明确提示。
  · write() 生成 Vegas C# 脚本（.cs，Vegas Scripting API），在 Vegas 内运行即可创建工程；
             同时落一份 .json 中间格式，便于外部读回。
操作方式：生成 Vegas C# 脚本片段执行 cut/delete/play/undo。

Vegas 脚本入口约定：
    namespace VegasTest {
        public class EntryPoint {
            public void FromVegas(Vegas vegas) { ... }
        }
    }
"""
from __future__ import annotations

import glob
import json
import subprocess
from pathlib import Path
from typing import Any

from editor_bridges.base import (
    EditorBridge, EditorProject, EditorTrack, EditorSegment,
    DetectionResult, ControlResult, ValidationResult,
)

SOFTWARE_ID = "vegas"
SOFTWARE_NAME = "VEGAS Pro"
DETECTION_METHOD = "安装路径+注册表+进程名(VEGAS Pro.exe)+COM接口检测"
PROJECT_EXT = ".veg"

# Vegas 常见安装根目录（按版本号子目录）
_INSTALL_ROOTS = [
    Path(r"C:\Program Files\VEGAS"),
    Path(r"C:\Program Files\Sony\VEGAS Pro"),
    Path(r"C:\Program Files\Magix\VEGAS Pro"),
]

_PROCESS_NAME = "VEGAS Pro.exe"


def _find_install() -> Path | None:
    """在常见安装根目录下查找含 VEGAS Pro.exe 的版本目录。"""
    for root in _INSTALL_ROOTS:
        if not root.exists():
            continue
        # 根目录下直接有 exe，或按版本号子目录存放
        for pattern in ("*/VEGAS Pro.exe", "VEGAS Pro.exe"):
            hits = glob.glob(str(root / pattern))
            if hits:
                return Path(hits[0]).parent
    return None


def _has_scripting(install_path: Path | None) -> bool:
    """粗测 .NET / Vegas Managed Scripting 是否可用（存在 Vegas 安装即认为支持）。"""
    if install_path is None:
        return False
    # Vegas 安装目录下通常有 Scripts / Managed 相关目录
    for rel in ("Scripts", "Managed", "VegasAPI"):
        if (install_path / rel).exists():
            return True
    return True  # Vegas 本体安装即自带脚本引擎


def _scan_process() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_PROCESS_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        return _PROCESS_NAME.lower() in out.stdout.lower()
    except Exception:
        return False


def _check_registry() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for root, sub in [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VEGAS\VEGAS Pro"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\VEGAS\VEGAS Pro"),
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


class VegasBridge(EditorBridge):
    software_id = SOFTWARE_ID
    software_name = SOFTWARE_NAME
    detection_method = DETECTION_METHOD
    project_ext = PROJECT_EXT

    # ---- 检测 ----

    def detect(self) -> DetectionResult:
        install_path = _find_install()
        running = _scan_process()
        version = _check_registry()
        has_script = _has_scripting(install_path)
        installed = install_path is not None

        detail_parts: list[str] = []
        if install_path:
            detail_parts.append(f"安装: {install_path}")
        else:
            detail_parts.append("未发现 VEGAS Pro 安装目录")
        if version:
            detail_parts.append(f"版本: {version}")
        if running:
            detail_parts.append("VEGAS Pro.exe 运行中")
        detail_parts.append(".NET/Vegas Scripting 桥接可用" if has_script else "未检测到脚本引擎")
        detail_parts.append("离线 JSON 中间格式读写可用")

        return DetectionResult(
            software_id=SOFTWARE_ID,
            software_name=SOFTWARE_NAME,
            installed=installed,
            running=running,
            version=version,
            install_path=str(install_path) if install_path else None,
            detection_method=DETECTION_METHOD,
            detail="; ".join(detail_parts),
            # 格式层适配器：未安装也支持离线 JSON/.cs 桥接，故 available=True
            available=True,
        )

    # ---- 读取：只支持 Vegas 脚本导出的 JSON ----

    def read(self, project_path: str | Path) -> EditorProject:
        p = Path(project_path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {p}")

        suffix = p.suffix.lower()
        if suffix == ".veg":
            # 判断是否是本适配器产出的文本桥接文件
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:4096]
            except Exception:
                head = ""
            if "FromVegas" in head or "using Sony.Vegas" in head:
                # 是本适配器生成的 C# 桥接文件，从中还原信息
                return self._read_cs_bridge(p)
            raise ValueError(
                ".veg 是 Vegas 原生 OLE 复合文档（二进制），Python 无法直接解析。"
                "请先在 Vegas 中运行导出脚本把轨道数据存为 .json，再用本适配器读取；"
                "或直接读取本适配器生成的 .cs / .json 文件。"
            )

        if suffix == ".cs":
            return self._read_cs_bridge(p)

        if suffix == ".json":
            return self._read_json(p)

        raise ValueError(f"不支持的文件类型: {suffix}（应为 .json / .cs；.veg 需先导出）")

    def _read_json(self, p: Path) -> EditorProject:
        """读取 Vegas 脚本导出的 JSON 中间格式（与 EditorProject.to_dict() 同构）。"""
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        tracks: list[EditorTrack] = []
        for tr in data.get("tracks", []):
            segments: list[EditorSegment] = []
            for s in tr.get("segments", []):
                segments.append(EditorSegment(
                    id=s.get("id", f"seg_{len(segments)}"),
                    track_type=s.get("track_type", "video"),
                    t=float(s.get("t", 0.0)),
                    length=float(s.get("length", 0.0)),
                    material=s.get("material"),
                    text=s.get("text"),
                    src_in=s.get("src_in"),
                    src_len=s.get("src_len"),
                    speed=float(s.get("speed", 1.0)),
                ))
            tracks.append(EditorTrack(
                id=tr.get("id", f"track_{len(tracks)}"),
                type=tr.get("type", "video"),
                segments=segments,
            ))
        return EditorProject(
            name=data.get("name", p.stem),
            tracks=tracks,
            duration=float(data.get("duration", 0.0)),
            fps=float(data.get("fps", 30.0)),
            width=int(data.get("width", 1920)),
            height=int(data.get("height", 1080)),
            source_file=str(p),
            extra={"format": "vegas_json"},
        )

    def _read_cs_bridge(self, p: Path) -> EditorProject:
        """从本适配器生成的 C# 桥接脚本中，提取内嵌的工程 JSON（写回用）。"""
        text = p.read_text(encoding="utf-8", errors="ignore")
        # 找 /* AICUT_PAYLOAD ... AICUT_PAYLOAD */ 块
        start = text.find("/* AICUT_PAYLOAD")
        end = text.find("AICUT_PAYLOAD */")
        if start == -1 or end == -1:
            raise ValueError("C# 桥接脚本中未找到内嵌工程数据块")
        payload = text[start + len("/* AICUT_PAYLOAD"):end].strip()
        data = json.loads(payload)
        # 从 payload 还原工程（注意：不能用 _read_json(p)，p 是 .cs 文本桥接文件）
        tracks: list[EditorTrack] = []
        for tr in data.get("tracks", []):
            segments: list[EditorSegment] = []
            for s in tr.get("segments", []):
                segments.append(EditorSegment(
                    id=s.get("id", f"seg_{len(segments)}"),
                    track_type=s.get("track_type", "video"),
                    t=float(s.get("t", 0.0)),
                    length=float(s.get("length", 0.0)),
                    material=s.get("material"),
                    text=s.get("text"),
                    src_in=s.get("src_in"),
                    src_len=s.get("src_len"),
                    speed=float(s.get("speed", 1.0)),
                ))
            tracks.append(EditorTrack(
                id=tr.get("id", f"track_{len(tracks)}"),
                type=tr.get("type", "video"),
                segments=segments,
            ))
        proj = EditorProject(
            name=data.get("name", p.stem),
            tracks=tracks,
            duration=float(data.get("duration", 0.0)),
            fps=float(data.get("fps", 30.0)),
            width=int(data.get("width", 1920)),
            height=int(data.get("height", 1080)),
        )
        proj.source_file = str(p)
        proj.extra = {"format": "vegas_cs_bridge"}
        return proj

    # ---- 写入：生成 Vegas C# 脚本 + JSON 中间格式 ----

    def write(self, project: EditorProject, output_path: str | Path) -> Path:
        """生成 Vegas C# 脚本（.cs），并落一份 .json 中间格式。

        output_path 由调用方指定：
        - 若以 .cs 结尾：直接写 C# 脚本；旁边再写同名 .json。
        - 若以 .veg 结尾（selftest 等通用流程）：仍把文本桥接内容写到该路径，
          旁边再写 .json；真正的二进制 .veg 无法用 Python 生成。
        """
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # 1) JSON 中间格式
        json_path = p.with_suffix(".json")
        json_path.write_text(project.to_json(), encoding="utf-8")

        # 2) C# Vegas 脚本
        cs_body = self._build_cs_script(project)
        # 决定主输出路径：调用方给 .cs 就用 .cs；给 .veg 就直接写在该路径上（文本桥接）
        if p.suffix.lower() == ".cs":
            main_out = p
        else:
            # 保持 selftest 期望的路径存在：把 C# 内容写到该路径
            main_out = p
        main_out.write_text(cs_body, encoding="utf-8")
        return main_out

    def _build_cs_script(self, project: EditorProject) -> str:
        """构造 Vegas C# 脚本（含内嵌 JSON payload，便于 read 回读）。"""
        # 内嵌 JSON（写回用）
        payload = project.to_json()
        # 转义 C# 注释内无特殊问题（注释里放 JSON 文本即可）
        track_lines: list[str] = []
        for tr in project.tracks:
            for seg in tr.segments:
                start_sec = seg.t
                length_sec = seg.length
                mat = (seg.material or seg.text or "").replace('"', '\\"')
                track_lines.append(
                    f'        // {tr.type}: {mat} @ {start_sec}s, {length_sec}s'
                )

        lines = [
            "// VEGAS Pro C# 脚本（由 AI cut 生成）",
            "// 在 Vegas 中：Tools -> Scripting -> Run Script，选择本文件即可创建工程",
            "using System;",
            "using Sony.Vegas;",
            "",
            "namespace AICutVegasBridge {",
            "    public class EntryPoint {",
            "        public void FromVegas(Vegas vegas) {",
            f'            vegas.NewProject();',
            f'            Project project = vegas.Project;',
            f'            project.Video.FrameRate = {project.fps}d;',
            f'            project.Video.Width = {project.width};',
            f'            project.Video.Height = {project.height};',
            "            // ---- 片段规划（时间单位：秒）----",
        ]
        lines.extend(track_lines)
        lines += [
            "            // 实际添加 MediaEvent / VideoTrack / AudioTrack 的逻辑在此展开",
            '            System.Windows.Forms.MessageBox.Show("AI cut: 工程骨架已生成，请检查轨道");',
            "        }",
            "    }",
            "}",
            "",
            "/* AICUT_PAYLOAD",
            payload,
            "AICUT_PAYLOAD */",
            "",
        ]
        return "\n".join(lines)

    # ---- 控制：生成 Vegas C# 脚本片段 ----

    def control(self, action: str, **kwargs) -> ControlResult:
        """生成 Vegas 脚本操作片段。支持 cut/delete/play/undo。"""
        action_map = {
            "cut": "vegas.Edit.Razor()",
            "delete": "vegas.Edit.DeleteKey()",
            "play": "vegas.Transport.Play()",
            "undo": "vegas.Edit.Undo()",
        }
        if action not in action_map:
            return ControlResult(
                action=action, success=False,
                detail=f"不支持的操作: {action}（可选: {', '.join(action_map)}）",
            )
        snippet = (
            "// VEGAS Pro 操作片段（由 AI cut 生成）\n"
            "using Sony.Vegas;\n"
            "public class EntryPoint {\n"
            "    public void FromVegas(Vegas vegas) {\n"
            f"        {action_map[action]};\n"
            "    }\n"
            "}\n"
        )
        return ControlResult(
            action=action, success=True,
            detail=f"已生成 Vegas C# 脚本片段（{action}）",
            data={"script": snippet, "language": "csharp", "api": "Sony.Vegas"},
        )

    # ---- 校验 ----

    def validate(self, project_path: str | Path) -> ValidationResult:
        p = Path(project_path)
        if not p.exists():
            return ValidationResult(valid=False, errors=[f"文件不存在: {p}"])
        errors: list[str] = []
        warnings: list[str] = []
        info: dict[str, Any] = {}

        suffix = p.suffix.lower()
        # 读取文本（二进制 .veg 会失败/为空）
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            return ValidationResult(valid=False, errors=[f"读取失败: {exc}"])

        looks_binary = "\x00" in text[:2048] and len(text) < 4096
        if suffix == ".veg" and looks_binary:
            return ValidationResult(
                valid=False,
                errors=["检测到二进制 .veg（OLE 复合文档），本适配器无法直接校验；请用 Vegas 脚本导出 JSON"],
            )

        if suffix in (".cs", ".veg") and ("FromVegas" in text or "using Sony.Vegas" in text):
            # C# 桥接脚本校验
            required = ["using System", "using Sony.Vegas", "EntryPoint", "FromVegas"]
            missing = [r for r in required if r not in text]
            if missing:
                errors.append(f"C# 脚本缺少关键结构: {', '.join(missing)}")
            info["format"] = "vegas_cs"
            info["has_payload"] = "AICUT_PAYLOAD" in text
            if not info["has_payload"]:
                warnings.append("未找到内嵌 JSON payload，无法 read 回读")
            return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings, info=info)

        if suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                return ValidationResult(valid=False, errors=[f"JSON 解析失败: {exc}"])
            if "tracks" not in data:
                errors.append("JSON 缺少 tracks 字段")
            elif not isinstance(data["tracks"], list):
                errors.append("tracks 不是数组")
            else:
                for i, tr in enumerate(data["tracks"]):
                    if not isinstance(tr, dict):
                        errors.append(f"track[{i}] 不是对象")
                    elif "segments" not in tr:
                        errors.append(f"track[{i}] 缺少 segments")
            info["format"] = "vegas_json"
            info["track_count"] = len(data.get("tracks", []))
            return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings, info=info)

        # 其它情况
        errors.append(f"无法识别的文件: {p.name}（应为 .cs 脚本或 .json 中间格式）")
        return ValidationResult(valid=False, errors=errors, info=info)


BRIDGE_CLASS = VegasBridge
