r"""editor_bridges · 剪辑软件隔离适配器基类与统一 IR。

每个剪辑软件一个独立适配器目录（lib/editor_bridges/<software>/），
继承 EditorBridge 并实现 detect / read / write / control 四个抽象方法。
各软件检测方式不同（窗口+快捷键 / 官方 API / 工程文件解析），互不耦合。

统一 IR（EditorProject）是所有适配器之间的中间表示：
- 时间单位：秒（float）
- 轨道类型：video / audio / text / effect / filter / sticker
- 片段字段：id, t, length, material, text, src_in, src_len, speed
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ============================================================
# 统一 IR 数据类
# ============================================================

@dataclass
class EditorSegment:
    """时间线上的一个片段（统一 IR）。"""
    id: str
    track_type: str          # video / audio / text / effect / filter / sticker
    t: float                 # 起点（秒）
    length: float            # 时长（秒）
    material: str | None = None   # 素材名称/路径
    text: str | None = None       # 字幕文本（text 轨道）
    src_in: float | None = None   # 素材入点（秒）
    src_len: float | None = None  # 素材引用时长（秒）
    speed: float = 1.0
    volume: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def end(self) -> float:
        return round(self.t + self.length, 6)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["end"] = self.end
        return d


@dataclass
class EditorTrack:
    """一条轨道（统一 IR）。"""
    id: str
    type: str                # video / audio / text / effect / filter / sticker
    segments: list[EditorSegment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "segments": [s.to_dict() for s in self.segments],
        }


@dataclass
class EditorProject:
    """一个剪辑工程（统一 IR）。所有适配器 read 返回它，write 接收它。"""
    name: str
    tracks: list[EditorTrack] = field(default_factory=list)
    duration: float = 0.0
    fps: float = 30.0
    width: int = 1920
    height: int = 1080
    source_file: str | None = None   # 原始工程文件路径
    extra: dict[str, Any] = field(default_factory=dict)

    def segments_by_type(self, track_type: str) -> list[EditorSegment]:
        out: list[EditorSegment] = []
        for tr in self.tracks:
            if tr.type == track_type:
                out.extend(tr.segments)
        return out

    @property
    def all_segments(self) -> list[EditorSegment]:
        out: list[EditorSegment] = []
        for tr in self.tracks:
            out.extend(tr.segments)
        return out

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration": round(self.duration, 6),
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "source_file": self.source_file,
            "tracks": [t.to_dict() for t in self.tracks],
            "extra": self.extra,
        }

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, **kw)


# ============================================================
# 检测 / 控制 / 校验 结果
# ============================================================

@dataclass
class DetectionResult:
    """软件检测结果。"""
    software_id: str
    software_name: str
    installed: bool
    running: bool = False
    version: str | None = None
    install_path: str | None = None
    project_path: str | None = None   # 当前打开的工程（如能检测到）
    detection_method: str = ""        # 用了什么检测方式
    detail: str = ""
    available: bool = False           # installed 且（running 或支持离线工程读写）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ControlResult:
    """操作执行结果。"""
    action: str
    success: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    executed: bool = False   # 是否真正执行了操作（False=仅生成脚本/未执行）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationResult:
    """工程文件校验结果。"""
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 适配器基类
# ============================================================

class EditorBridge(ABC):
    """剪辑软件隔离适配器基类。

    子类必须设置类属性：
      software_id   短标识，如 "jianying" / "premiere"
      software_name 显示名，如 "剪映专业版" / "Adobe Premiere Pro"
      detection_method  检测方式描述，如 "窗口标题+进程名+快捷键" / "官方 Python API" / "工程文件扩展名"
      project_ext   原生工程文件扩展名，如 ".json" / ".prproj" / ".fcpxml"
    """

    software_id: str = ""
    software_name: str = ""
    detection_method: str = ""
    project_ext: str = ""

    # ---- 抽象方法 ----

    @abstractmethod
    def detect(self) -> DetectionResult:
        """检测软件是否安装/运行。每软件用自己的方式，互不影响。"""

    @abstractmethod
    def read(self, project_path: str | Path) -> EditorProject:
        """读取原生工程文件 → 统一 IR。"""

    @abstractmethod
    def write(self, project: EditorProject, output_path: str | Path) -> Path:
        """统一 IR → 原生工程文件，返回写入路径。"""

    @abstractmethod
    def control(self, action: str, **kwargs) -> ControlResult:
        """执行软件操作（剪切/删除/加字幕等）。
        action 可选值由各适配器定义，至少支持：cut / delete / play / undo。
        """

    # ---- 默认实现（可覆盖）----

    def validate(self, project_path: str | Path) -> ValidationResult:
        """校验原生工程文件合法性。默认尝试 read 并检查基本字段。"""
        errors: list[str] = []
        warnings: list[str] = []
        info: dict[str, Any] = {}
        p = Path(project_path)
        if not p.exists():
            return ValidationResult(valid=False, errors=[f"文件不存在: {p}"])
        try:
            proj = self.read(p)
            info["name"] = proj.name
            info["track_count"] = len(proj.tracks)
            info["segment_count"] = len(proj.all_segments)
            info["duration"] = proj.duration
            if not proj.tracks:
                warnings.append("工程无轨道")
            if proj.duration <= 0:
                warnings.append("工程时长为 0")
            return ValidationResult(valid=True, warnings=warnings, info=info)
        except Exception as exc:
            return ValidationResult(valid=False, errors=[f"读取失败: {exc}"])

    def list_projects(self, directory: str | Path) -> list[Path]:
        """列出目录下该软件的工程文件。"""
        d = Path(directory)
        if not d.is_dir():
            return []
        ext = self.project_ext.lower()
        return sorted([f for f in d.rglob("*") if f.is_file() and f.suffix.lower() == ext])

    def selftest(self) -> dict[str, Any]:
        """适配器自检：检测 + 生成最小工程 + 读写往返 + 校验。
        返回 {software_id, detect, roundtrip, validate, errors}。
        """
        errors: list[str] = []
        det = self.detect()
        roundtrip_ok = False
        validate_ok = False
        tmp_proj: Path | None = None
        try:
            # 构造最小工程
            seg = EditorSegment(
                id="seg_001", track_type="video", t=0.0, length=5.0,
                material="test.mp4", src_in=0.0, src_len=5.0,
            )
            track = EditorTrack(id="trk_001", type="video", segments=[seg])
            proj = EditorProject(
                name=f"selftest_{self.software_id}",
                tracks=[track], duration=5.0, fps=30.0,
                width=1920, height=1080,
            )
            import tempfile
            tmpdir = Path(tempfile.mkdtemp(prefix=f"eb_{self.software_id}_"))
            tmp_proj = tmpdir / f"test{self.project_ext}"
            self.write(proj, tmp_proj)
            if tmp_proj.exists():
                roundtrip_ok = True
                vr = self.validate(tmp_proj)
                validate_ok = vr.valid
                if not vr.valid:
                    errors.extend(vr.errors)
            else:
                errors.append("write 后文件不存在")
        except Exception as exc:
            errors.append(f"selftest 异常: {exc}")
        return {
            "software_id": self.software_id,
            "software_name": self.software_name,
            "detect": det.to_dict(),
            "roundtrip": roundtrip_ok,
            "validate": validate_ok,
            "errors": errors,
        }
