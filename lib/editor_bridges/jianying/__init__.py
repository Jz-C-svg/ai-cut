r"""剪映专业版适配器。

检测方式：窗口标题 + 进程名（JianyingPro.exe / 剪映专业版.exe）+ 草稿目录扫描。
读写方式：明文草稿 JSON（draft_content.json），复用 aicut.draft.Draft 引擎。
操作方式：窗口激活 + SendInput 快捷键（Ctrl+B 分割等）。

注意：剪映原生 draft_content.json 是加密/混淆的，本项目只读写 06-drafts 下的明文副本。
"""
from __future__ import annotations
from aicut.portability import portable_path

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from editor_bridges.base import (
    EditorBridge, EditorProject, EditorTrack, EditorSegment,
    DetectionResult, ControlResult, ValidationResult,
)

SOFTWARE_ID = "jianying"
SOFTWARE_NAME = "剪映专业版"
DETECTION_METHOD = "窗口标题+进程名+草稿目录扫描"
PROJECT_EXT = ".json"

# 剪映常见安装路径
_INSTALL_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro",
    Path(portable_path('user:AppData/Local/JianyingPro')),
]

# 草稿根目录（项目内 06-drafts）
_PROJECT_DRAFTS = Path(portable_path('project:06-drafts'))


def _scan_processes() -> list[dict[str, str]]:
    """扫描当前运行的剪映进程。"""
    found: list[dict[str, str]] = []
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        for line in out.stdout.strip().splitlines():
            if "JianyingPro" in line:
                found.append({"process": "JianyingPro.exe", "detail": line.strip()})
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq 剪映专业版.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        for line in out.stdout.strip().splitlines():
            if "剪映" in line:
                found.append({"process": "剪映专业版.exe", "detail": line.strip()})
    except Exception:
        pass
    return found


def _find_install() -> Path | None:
    """查找剪映安装目录。"""
    for cand in _INSTALL_CANDIDATES:
        if cand.exists():
            return cand
    return None


def _scan_drafts() -> list[Path]:
    """扫描项目内 06-drafts 下的明文草稿。"""
    if not _PROJECT_DRAFTS.exists():
        return []
    return sorted([
        d for d in _PROJECT_DRAFTS.iterdir()
        if d.is_dir() and (d / "draft_content.json").exists()
    ])


class JianYingBridge(EditorBridge):
    software_id = SOFTWARE_ID
    software_name = SOFTWARE_NAME
    detection_method = DETECTION_METHOD
    project_ext = PROJECT_EXT

    def detect(self) -> DetectionResult:
        install_path = _find_install()
        processes = _scan_processes()
        drafts = _scan_drafts()
        running = len(processes) > 0
        installed = install_path is not None
        version = None
        if install_path:
            # 尝试从安装目录读版本
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
        if drafts:
            detail_parts.append(f"草稿: {len(drafts)} 个")
        return DetectionResult(
            software_id=SOFTWARE_ID,
            software_name=SOFTWARE_NAME,
            installed=installed,
            running=running,
            version=version,
            install_path=str(install_path) if install_path else None,
            project_path=str(drafts[0]) if drafts else None,
            detection_method=DETECTION_METHOD,
            detail="; ".join(detail_parts),
            available=installed or len(drafts) > 0,
        )

    def read(self, project_path: str | Path) -> EditorProject:
        """读取剪映明文草稿 → 统一 IR。复用 aicut.draft.Draft。"""
        from aicut.draft import Draft as JyDraft

        p = Path(project_path)
        if p.is_dir():
            p = p / "draft_content.json"
        jy = JyDraft.load(p)

        tracks: list[EditorTrack] = []
        for tr in jy.tracks:
            segments: list[EditorSegment] = []
            for seg in tr.segments:
                es = EditorSegment(
                    id=seg.id,
                    track_type=seg.track_type,
                    t=seg.t,
                    length=seg.length,
                    material=seg.material_path() or seg.material_name() or None,
                    text=seg.text,
                    src_in=seg.src_in,
                    src_len=seg.src_len,
                    speed=seg.speed,
                    volume=seg.raw.get("volume"),
                )
                segments.append(es)
            tracks.append(EditorTrack(id=tr.id, type=tr.type, segments=segments))

        return EditorProject(
            name=jy.name,
            tracks=tracks,
            duration=jy.declared_duration,
            fps=float(jy.content.get("fps", 30)),
            width=int(jy.content.get("canvas_config", {}).get("width", 1920)),
            height=int(jy.content.get("canvas_config", {}).get("height", 1080)),
            source_file=str(p),
            extra={"raw_track_count": len(jy.tracks)},
        )

    def write(self, project: EditorProject, output_path: str | Path) -> Path:
        """统一 IR → 剪映明文草稿 JSON。

        生成最小可用的 draft_content.json 结构，包含轨道、片段、素材引用。
        """
        import time
        from aicut.draft import new_id, to_us

        p = Path(output_path)
        if p.suffix.lower() != ".json":
            p = p / "draft_content.json"
        from .integrity import validate_ir
        validate_ir(project)
        # This lightweight IR writer must never silently discard native decoration.
        unsupported = []
        for seg in project.all_segments:
            for key, value in seg.extra.items():
                if value is not None and value != [] and value != {}:
                    unsupported.append(f"{seg.id}.{key}")
        if unsupported:
            raise ValueError("IR writer cannot serialize: " + ", ".join(unsupported)
                             + "; use build_draft.py's native sheet writer for Jianying/CapCut")
        p.parent.mkdir(parents=True, exist_ok=True)

        now_us = int(time.time() * 1_000_000)

        # 构建素材字典（按类型分组，剪映草稿格式要求）
        materials: dict[str, list[dict]] = {}
        material_ids: dict[tuple[str, str], str] = {}
        for seg in project.all_segments:
            mat_key = seg.text if seg.track_type == "text" else seg.material
            typed_key = (seg.track_type, mat_key)
            if mat_key and typed_key not in material_ids:
                mid = new_id()
                material_ids[typed_key] = mid
                mtype = "text" if seg.track_type == "text" else ("video" if seg.track_type == "video" else seg.track_type)
                mat_dict: dict[str, Any] = {
                    "id": mid,
                    "material_name": mat_key,
                    "path": mat_key if seg.track_type != "text" else "",
                    "duration": to_us(max(seg.length, (seg.src_in or 0) + (seg.src_len if seg.src_len is not None else seg.length * seg.speed))),
                }
                if seg.track_type == "text" and seg.text:
                    mat_dict["content"] = json.dumps({"text": seg.text}, ensure_ascii=False)
                    mat_dict["material_name"] = seg.text[:20]
                materials.setdefault({"video": "videos", "audio": "audios", "text": "texts"}.get(mtype, mtype), []).append(mat_dict)

        # One source can be referenced by multiple trims; retain its furthest used endpoint.
        for seg in project.all_segments:
            key = (seg.track_type, seg.text if seg.track_type == "text" else seg.material)
            mid = material_ids.get(key)
            for group in materials.values():
                for mat in group:
                    if mat["id"] == mid:
                        mat["duration"] = max(mat["duration"], to_us((seg.src_in or 0) + (seg.src_len if seg.src_len is not None else seg.length * seg.speed)))

        # 构建轨道
        tracks: list[dict] = []
        for tr in project.tracks:
            seg_list: list[dict] = []
            for seg in tr.segments:
                seg_dict: dict[str, Any] = {
                    "id": seg.id or new_id(),
                    "material_id": material_ids.get((seg.track_type, seg.material), ""),
                    "target_timerange": {
                        "start": to_us(seg.t),
                        "duration": to_us(seg.length),
                    },
                }
                if seg.src_in is not None and seg.src_len is not None:
                    seg_dict["source_timerange"] = {
                        "start": to_us(seg.src_in),
                        "duration": to_us(seg.src_len),
                    }
                if seg.speed != 1.0:
                    seg_dict["speed"] = seg.speed
                if seg.volume is not None:
                    seg_dict["volume"] = seg.volume
                if seg.track_type == "text" and seg.text:
                    seg_dict["material_id"] = material_ids.get(("text", seg.text), "")
                seg_list.append(seg_dict)
            tracks.append({
                "id": tr.id or new_id(),
                "type": tr.type,
                "segments": seg_list,
            })

        content = {
            "id": new_id(),
            "name": project.name,
            "duration": to_us(max([project.duration] + [seg.end for seg in project.all_segments])),
            "fps": project.fps,
            "canvas_config": {"width": project.width, "height": project.height},
            "tracks": tracks,
            "materials": materials,
            "create_time": now_us,
            "update_time": now_us,
        }

        from .integrity import prepare_assets, check_content
        if project.extra.get("self_contained_assets"):
            prepare_assets(content, p.parent, project.extra.get("media_root"))
        errors = check_content(content)
        if errors:
            raise ValueError("; ".join(errors))
        p.write_text(
            json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return p

    def control(self, action: str, **kwargs) -> ControlResult:
        """剪映窗口操作：激活窗口 + SendInput 快捷键。

        支持 action: cut(ctrl+b) / delete / play(space) / undo(ctrl+z) /
                   mark_in(i) / mark_out(o) / ripple_delete(shift+delete)
        """
        hotkeys = {
            "cut": "ctrl+b",
            "delete": "delete",
            "play": "space",
            "undo": "ctrl+z",
            "mark_in": "i",
            "mark_out": "o",
            "ripple_delete": "shift+delete",
            "zoom_fit": "shift+\\",
        }
        if action not in hotkeys:
            return ControlResult(
                action=action, success=False,
                detail=f"不支持的操作: {action}（可选: {', '.join(hotkeys)}）",
            )
        try:
            # 复用 ai-auto-editor 的窗口控制
            import sys
            skill_path = portable_path('user:AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/.user_skills/ai-auto-editor/scripts')
            if skill_path not in sys.path:
                sys.path.insert(0, skill_path)
            from aieditor.app_control import activate_editor, send_hotkey
            from aieditor.editor_programs import parse_hotkey

            hwnd = activate_editor("capcut")
            if not hwnd:
                return ControlResult(action=action, success=False, detail="未找到剪映窗口")
            vks = parse_hotkey(hotkeys[action])
            send_hotkey(vks)
            return ControlResult(
                action=action, success=True,
                detail=f"已发送 {hotkeys[action]} 到剪映窗口",
                data={"hwnd": str(hwnd)},
            )
        except Exception as exc:
            return ControlResult(action=action, success=False, detail=f"操作失败: {exc}")

    def validate(self, project_path: str | Path) -> ValidationResult:
        """校验剪映草稿：必须有 tracks 数组，每个 track 有 id/type/segments。"""
        p = Path(project_path)
        if p.is_dir():
            p = p / "draft_content.json"
        if not p.exists():
            return ValidationResult(valid=False, errors=[f"文件不存在: {p}"])
        errors: list[str] = []
        warnings: list[str] = []
        info: dict[str, Any] = {}
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return ValidationResult(valid=False, errors=[f"JSON 解析失败: {exc}"])
        from .integrity import check_content
        errors.extend(check_content(data))
        warnings.append("Internal draft validation only; Jianying host import remains unverified")
        if "tracks" not in data:
            errors.append("缺少 tracks 字段")
        elif not isinstance(data["tracks"], list):
            errors.append("tracks 不是数组")
        else:
            for i, tr in enumerate(data["tracks"]):
                if not isinstance(tr, dict):
                    errors.append(f"track[{i}] 不是对象")
                    continue
                if "id" not in tr:
                    errors.append(f"track[{i}] 缺少 id")
                if "type" not in tr:
                    errors.append(f"track[{i}] 缺少 type")
                if "segments" not in tr:
                    errors.append(f"track[{i}] 缺少 segments")
        if "duration" in data:
            info["duration_us"] = data["duration"]
        if "fps" in data:
            info["fps"] = data["fps"]
        info["track_count"] = len(data.get("tracks", []))
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            info=info,
        )


BRIDGE_CLASS = JianYingBridge
