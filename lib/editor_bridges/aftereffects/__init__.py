"""Adobe After Effects 适配器（格式层 / ExtendScript 桥接）。

检测方式：安装路径扫描（C:\\Program Files\\Adobe\\Adobe After Effects *）
          + HKLM 卸载注册表 + 进程名（AfterFX.exe）。

读写方式说明（重要）：
  .aep 是 Adobe 私有二进制格式，Python 无法直接解析/生成。
  本适配器采用「中间格式 + ExtendScript 桥接」策略：
    - write() 输出两份产物：
        1) 与 output_path 同名的 JSON 中间格式（Composition + Layers 数据，
           可被 read() 读回，用于离线往返）；
        2) 同名 .jsx ExtendScript 脚本，在 AE 中运行后会自动创建对应工程。
    - read()：
        * 传入 .json（或本适配器 write 生成的 JSON 中间格式）→ 解析为统一 IR；
        * 传入真实二进制 .aep → 抛出明确异常，提示先用 ExtendScript 导出 JSON。
  本机未安装 AE，detect 返回 installed=False，但 write/read/validate 仍可用。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from editor_bridges.base import (
    EditorBridge, EditorProject, EditorTrack, EditorSegment,
    DetectionResult, ControlResult, ValidationResult,
)

SOFTWARE_ID = "aftereffects"
SOFTWARE_NAME = "Adobe After Effects"
DETECTION_METHOD = "注册表+安装路径扫描+进程名(AfterFX.exe)"
PROJECT_EXT = ".aep"

_INSTALL_GLOBS = [
    Path("C:/Program Files/Adobe"),
    Path("C:/Program Files (x86)/Adobe"),
]
_PROCESS_NAME = "AfterFX.exe"
_REG_KEYWORDS = ["Adobe After Effects"]


def _scan_install() -> tuple[Path | None, str | None]:
    """扫描常见安装路径，返回 (安装目录, 版本号)。"""
    for root in _INSTALL_GLOBS:
        if not root.exists():
            continue
        for child in sorted(root.glob("Adobe After Effects*")):
            if child.is_dir() and (child / _PROCESS_NAME).exists():
                ver = child.name.replace("Adobe After Effects", "").strip() or None
                return child, ver
    return None, None


def _scan_registry() -> tuple[Path | None, str | None]:
    """扫描 HKLM 卸载注册表，查找 After Effects 安装记录。"""
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
    """检测 AE 进程是否在运行。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_PROCESS_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        return _PROCESS_NAME.lower() in out.stdout.lower()
    except Exception:
        return False


class AfterEffectsBridge(EditorBridge):
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
            detail_parts.append("未发现 AE 安装（格式层通过 .jsx + JSON 中间格式桥接）")
        return DetectionResult(
            software_id=SOFTWARE_ID,
            software_name=SOFTWARE_NAME,
            installed=installed,
            running=running,
            version=version,
            install_path=str(install_path) if install_path else None,
            detection_method=DETECTION_METHOD,
            detail="; ".join(detail_parts),
            # 格式层支持离线生成桥接脚本与中间 JSON，故标记 available
            available=True,
        )

    # ---- 读：JSON 中间格式 → 统一 IR ----

    def read(self, project_path: str | Path) -> EditorProject:
        p = Path(project_path)
        if not p.exists():
            raise FileNotFoundError(f"工程文件不存在: {p}")

        # .aep 是二进制私有格式：先尝试按 JSON 中间格式解析；
        # 若解析失败，说明是真实二进制 .aep，抛出明确异常。
        if p.suffix.lower() == ".aep":
            try:
                data = json.loads(p.read_text(encoding="utf-8-sig"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise NotImplementedError(
                    ".aep 是 Adobe 私有二进制格式，无法用 Python 直接解析。"
                    "请先在 AE 中运行导出脚本，把合成/图层数据导出为 .json 后再读取。"
                )
        elif p.suffix.lower() == ".json":
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        else:
            raise ValueError(
                f"不支持的文件类型: {p.suffix}。请提供 .json 中间格式"
                "（由 AE 导出脚本生成，或由本适配器 write 生成）。"
            )

        comp = data.get("composition", data)
        tracks: list[EditorTrack] = []
        # layers 是扁平列表，按 layer.type 归到对应轨道
        layers = comp.get("layers", [])
        grouped: dict[str, list[EditorSegment]] = {}
        for layer in layers:
            ltype = layer.get("type", "video")
            seg = EditorSegment(
                id=str(layer.get("id", layer.get("name", ""))),
                track_type=ltype,
                t=float(layer.get("start", 0.0)),
                length=float(layer.get("duration", 0.0)),
                material=layer.get("source") or layer.get("name"),
                text=layer.get("text"),
                src_in=layer.get("src_in"),
                src_len=layer.get("src_len"),
                speed=float(layer.get("speed", 1.0)),
            )
            grouped.setdefault(ltype, []).append(seg)
        for ttype, segs in grouped.items():
            tracks.append(EditorTrack(id=f"{ttype}_track", type=ttype, segments=segs))

        return EditorProject(
            name=comp.get("name", p.stem),
            tracks=tracks,
            duration=float(comp.get("duration", 0.0)),
            fps=float(comp.get("fps", 30.0)),
            width=int(comp.get("width", 1920)),
            height=int(comp.get("height", 1080)),
            source_file=str(p),
            extra={"format": "ae-jsx-bridge/json", "layers": len(layers)},
        )

    # ---- 写：统一 IR → JSON 中间格式 + ExtendScript(.jsx) ----

    def write(self, project: EditorProject, output_path: str | Path) -> Path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # 1) 把统一 IR 拍平为 layers 列表（AE 合成是图层列表，不区分轨道）
        layers: list[dict[str, Any]] = []
        for tr in project.tracks:
            for seg in tr.segments:
                layer: dict[str, Any] = {
                    "id": seg.id,
                    "name": seg.material or seg.text or seg.id,
                    "type": tr.type,
                    "start": round(seg.t, 6),
                    "duration": round(seg.length, 6),
                    "source": seg.material,
                    "speed": seg.speed,
                }
                if seg.text:
                    layer["text"] = seg.text
                if seg.src_in is not None:
                    layer["src_in"] = round(seg.src_in, 6)
                if seg.src_len is not None:
                    layer["src_len"] = round(seg.src_len, 6)
                layers.append(layer)

        comp = {
            "name": project.name or "Comp.1",
            "duration": round(project.duration, 6),
            "fps": project.fps,
            "width": project.width,
            "height": project.height,
            "layers": layers,
        }
        intermediate = {"composition": comp, "generator": "aicut-aftereffects-bridge"}

        # 2) JSON 中间格式写到 output_path（self-test 会检查此文件存在并校验）
        p.write_text(
            json.dumps(intermediate, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 3) 生成同名 .jsx ExtendScript：在 AE 中运行后创建合成与图层
        jsx_path = p.with_suffix(".jsx") if p.suffix else (p.parent / (p.name + ".jsx"))
        jsx = self._build_jsx(comp)
        jsx_path.write_text(jsx, encoding="utf-8")

        return p

    def _build_jsx(self, comp: dict[str, Any]) -> str:
        """根据合成数据生成 AE ExtendScript。"""
        comp_json = json.dumps(comp, ensure_ascii=False)
        return (
            "// Adobe After Effects ExtendScript 桥接脚本\n"
            "// 由 AI cut 自动生成：在 AE 中通过 文件 > 脚本 > 运行脚本文件 执行\n"
            "var compData = " + comp_json + ";\n"
            "(function() {\n"
            "    app.beginUndoGroup('AI cut: 创建合成');\n"
            "    var proj = app.project || app.newProject();\n"
            "    var comp = proj.items.addComp(compData.name, compData.width, compData.height, 1.0, compData.duration, compData.fps);\n"
            "    comp.openInViewer();\n"
            "    for (var i = 0; i < compData.layers.length; i++) {\n"
            "        var ly = compData.layers[i];\n"
            "        var solid = comp.layers.addSolid([0.5, 0.5, 0.5], ly.name, comp.width, comp.height, 1.0, ly.duration);\n"
            "        solid.startTime = ly.start;\n"
            "        if (ly.text) {\n"
            "            var tl = comp.layers.addText(ly.text);\n"
            "            tl.startTime = ly.start;\n"
            "        }\n"
            "    }\n"
            "    app.endUndoGroup();\n"
            "})();\n"
        )

    # ---- 控制：生成 ExtendScript，不实际执行 ----

    def control(self, action: str, **kwargs) -> ControlResult:
        """生成在 AE 中执行的 ExtendScript(.jsx) 脚本字符串。

        支持 action: cut / delete / play / undo / open / render。
        本机未安装 AE，此处仅生成脚本内容，不执行。
        """
        script_map: dict[str, str] = {
            "cut": (
                "// 剪切：把当前时间指针处的素材分离（通过复制-删除近似）\n"
                "app.executeCommand(app.findMenuCommandId('Cut'));\n"
            ),
            "delete": (
                "// 删除选中图层\n"
                "app.executeCommand(app.findMenuCommandId('Clear'));\n"
            ),
            "play": (
                "// 播放预览\n"
                "app.executeCommand(app.findMenuCommandId('Play'));\n"
            ),
            "undo": (
                "// 撤销\n"
                "app.executeCommand(app.findMenuCommandId('Undo'));\n"
            ),
            "open": (
                "// 打开工程\n"
                "var path = '%s';\n"
                "app.open(new File(path));\n" % kwargs.get("project_path", "<project.aep>")
            ),
            "render": (
                "// 添加到渲染队列并渲染\n"
                "var comp = app.project.activeItem;\n"
                "if (comp && comp instanceof CompItem) {\n"
                "    var rq = app.project.renderQueue;\n"
                "    var item = rq.items.add(comp);\n"
                "    item.applyTemplate('Best Settings');\n"
                "    rq.render();\n"
                "}\n"
            ),
        }
        if action not in script_map:
            return ControlResult(
                action=action, success=False,
                detail=f"不支持的操作: {action}（可选: {', '.join(script_map)}）",
            )
        jsx = (
            "// Adobe After Effects ExtendScript 桥接脚本\n"
            "// 由 AI cut 自动生成，未实际执行，需在 AE 中运行\n"
            "app.beginSuppressDialogs();\n"
            "try {\n"
            + "\n".join("    " + ln for ln in script_map[action].splitlines())
            + "\n} catch (e) { alert('AE 脚本错误: ' + e); }\n"
            "app.endSuppressDialogs(false);\n"
        )
        return ControlResult(
            action=action, success=True,
            detail=f"已生成 {action} 操作的 ExtendScript（未实际执行）",
            data={"jsx": jsx, "format": "jsx", "executed": False},
        )

    # ---- 校验 ----

    def validate(self, project_path: str | Path) -> ValidationResult:
        """校验产物：.jsx 做括号配平；.json / 本适配器生成的 .aep(JSON中间格式) 校验结构。"""
        p = Path(project_path)
        if not p.exists():
            return ValidationResult(valid=False, errors=[f"文件不存在: {p}"])
        errors: list[str] = []
        warnings: list[str] = []
        info: dict[str, Any] = {}

        suffix = p.suffix.lower()

        # 情形 A：.jsx 脚本 → 基本括号配平检查
        if suffix == ".jsx":
            text = p.read_text(encoding="utf-8", errors="replace")
            for open_c, close_c in [("{", "}"), ("(", ")"), ("[", "]")]:
                if text.count(open_c) != text.count(close_c):
                    errors.append(
                        f"括号不配平: {open_c}{close_c} "
                        f"{text.count(open_c)} vs {text.count(close_c)}"
                    )
            info["format"] = "jsx"
            info["size"] = len(text)
            return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings, info=info)

        # 情形 B：.json 或本适配器 write 生成的 .aep(JSON) → 校验 JSON 结构
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return ValidationResult(
                valid=False,
                errors=[f"无法解析为 JSON 中间格式: {exc}（真实二进制 .aep 需先导出 JSON）"],
            )
        comp = data.get("composition")
        if comp is None:
            errors.append("缺少 composition 字段")
        else:
            layers = comp.get("layers", [])
            info["format"] = "ae-json"
            info["composition"] = comp.get("name")
            info["layer_count"] = len(layers)
            info["duration"] = comp.get("duration")
            if not layers:
                warnings.append("合成无图层")
            for i, ly in enumerate(layers):
                if "type" not in ly:
                    errors.append(f"layer[{i}] 缺少 type")
                if "start" not in ly:
                    errors.append(f"layer[{i}] 缺少 start")
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            info=info,
        )


BRIDGE_CLASS = AfterEffectsBridge
