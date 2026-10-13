# -*- coding: utf-8 -*-
"""成片工作流存档（skill_archive）：把一次跑通的决策单 sheet 存成可复用 Skill Archive JSON。

设计目标（对应 docs/外网先进方案评估.md 中 FireRed-OpenStoryline 的 skill archiving）：
  技能 = 结构化 JSON 描述 + 参数槽位 + 验收规则。运行时 restore 换素材即可复用，
  再交给 build_draft.py 出剪映草稿。

子命令：
  archive  从已有 03-scripts/*.sheet.json 提取可复用结构，生成存档 JSON
  restore  读存档 JSON，替换素材/转写路径，生成新的决策单 sheet
  list     列出 content-library/skills/ 下所有存档
  show     显示某个存档详情

用法：
  python scripts/skill_archive.py archive --sheet 03-scripts\\月子知识-成片.sheet.json ^
      --id koubo-zhishi-v1 --name "月子知识口播成片工作流" --tags 口播,知识类,月子
  python scripts/skill_archive.py restore --archive content-library\\skills\\koubo-zhishi-v1.json ^
      --video work\\normalized\\IMG_5840.mp4 --transcript 02-transcript\\IMG_5840-成片.auto.json ^
      --out 03-scripts\\IMG_5840-复用.sheet.json
  python scripts/skill_archive.py list
  python scripts/skill_archive.py show --archive content-library\\skills\\koubo-zhishi-v1.json

注意：restore 保留原 sheet 的素材时间码（start/end），假设新素材与源素材"同结构"。
     若新素材节奏不同，restore 后仍需人工/auto_edit 重新切镜头。
"""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut.paths import CONTENT_LIBRARY, SHEETS  # noqa: E402

ARCHIVE_DIR = CONTENT_LIBRARY / "skills"

# 结构代号 -> 中文段落名（用于 structure 字段的可读展示）
_STRUCT_MAP = {
    "hook": "钩子", "explain": "展开", "close": "落点",
    "lead": "开场", "body": "正文", "punch": "高潮",
    "cta": "行动指令", "outro": "结尾",
}


def _to_root_rel(p: str | Path) -> str:
    """路径尽量转成相对 ROOT 的写法（与既有 sheet 中 subtitles_json 约定一致）。"""
    p = Path(p)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _structure_labels(structure: str) -> list[str]:
    """'hook-explain-close' -> ['钩子','展开','落点']。"""
    if not structure:
        return []
    parts = [x.strip() for x in str(structure).split("-") if x.strip()]
    return [_STRUCT_MAP.get(x, x) for x in parts]


def _derive_parameters(sheet: dict) -> dict:
    """从 sheet clips / 全局设置里提取可复用的参数槽位。"""
    clips = sheet.get("clips") or []
    durs = []
    speeds = set()
    filters: dict[str, int] = {}
    for c in clips:
        try:
            d = float(c["end"]) - float(c["start"])
            if d > 0:
                durs.append(round(d, 3))
        except (KeyError, TypeError, ValueError):
            pass
        if c.get("speed"):
            speeds.add(float(c.get("speed")))
        f = c.get("滤镜")
        if f:
            filters[f] = int(c.get("滤镜强度", 0) or 0)

    global_fx = sheet.get("画面") or {}
    if global_fx.get("滤镜"):
        filters[str(global_fx["滤镜"])] = int(global_fx.get("滤镜强度", 0) or 0)

    return {
        "镜头最短秒": round(min(durs), 3) if durs else None,
        "镜头最长秒": round(max(durs), 3) if durs else None,
        "平均镜头秒": round(sum(durs) / len(durs), 3) if durs else None,
        "镜头数参考": len(clips),
        "画幅": f"{sheet.get('width')}x{sheet.get('height')}",
        "fps": sheet.get("fps"),
        "压缩倍速": (min(speeds) if speeds else 1.0),
        "滤镜": (list(filters.keys()) or [None])[0],
        "滤镜强度": (list(filters.values()) or [None])[0],
        "字幕每行最多字": 14,
        "BGM音量": 0.15,
    }


def _derive_quality_gates(sheet: dict) -> dict:
    clips = sheet.get("clips") or []
    total = 0.0
    for c in clips:
        try:
            total += float(c["end"]) - float(c["start"])
        except (KeyError, TypeError, ValueError):
            pass
    return {
        "min_clips": max(2, len(clips)),
        "max_duration_seconds": 180,
        "参考总镜头秒": round(total, 2),
        "require_cover": bool(sheet.get("cover_text")),
        "require_cta": bool(sheet.get("cta")),
    }


def _derive_hooks(sheet: dict) -> dict:
    check = sheet.get("自检") or {}
    span = check.get("风格区间秒") or []
    lead = float(span[0]) if span else 3.0
    return {"hook_lead_seconds": lead, "force_hook_first": False}


def archive(sheet_path: str | Path, name: str, sid: str,
            description: str = "", tags: list[str] | None = None) -> Path:
    """读取一个 sheet JSON，提取可复用结构，生成 Skill Archive JSON。"""
    sheet_path = Path(sheet_path)
    if not sheet_path.is_absolute():
        sheet_path = ROOT / sheet_path
    if not sheet_path.exists():
        raise FileNotFoundError(f"找不到决策单：{sheet_path}")

    sheet = _read_json(sheet_path)
    tags = tags or []
    description = description or f"{name}：从 {sheet_path.name} 提取的可复用成片结构"

    archive_doc = {
        "id": sid,
        "name": name,
        "description": description,
        "version": "1.0",
        "created": date.today().isoformat(),
        "tags": tags,
        "style_id": sheet.get("风格") or sheet.get("style"),
        "template": sheet.get("template"),
        "parameters": _derive_parameters(sheet),
        "text_style_preset": sheet.get("style"),
        "structure": _structure_labels(sheet.get("结构", "")),
        "structure_raw": sheet.get("结构", ""),
        "hooks": _derive_hooks(sheet),
        "quality_gates": _derive_quality_gates(sheet),
        "cover_text": sheet.get("cover_text", ""),
        "cta": sheet.get("cta", ""),
        "keywords": sheet.get("keywords", []),
        "source_sheet": _to_root_rel(sheet_path),
        # 内嵌整份 sheet 作为模板，restore 不依赖源 sheet 是否还在原地
        "sheet_template": deepcopy(sheet),
        "usage_example": (
            f"python scripts/skill_archive.py restore --archive {_to_root_rel(ARCHIVE_DIR / (sid + '.json'))} "
            "--video work/normalized/<新素材>.mp4 "
            "--transcript 02-transcript/<新素材>.auto.json "
            f"--out {_to_root_rel(SHEETS / '<新成片>.sheet.json')}"
        ),
    }

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = ARCHIVE_DIR / f"{sid}.json"
    _write_json(out, archive_doc)
    return out


def restore(archive_path: str | Path, new_video: str | Path,
            new_transcript: str | Path, output_sheet: str | Path,
            project: str | None = None) -> Path:
    """读存档，替换素材/转写路径，生成新的决策单 sheet。"""
    archive_path = Path(archive_path)
    if not archive_path.is_absolute():
        archive_path = ROOT / archive_path
    archive_doc = _read_json(archive_path)

    # 优先用内嵌模板，缺了再回退读 source_sheet
    template = archive_doc.get("sheet_template")
    if not template:
        src = ROOT / archive_doc["source_sheet"]
        template = _read_json(src)
    sheet = deepcopy(template)

    new_video = Path(new_video)
    if not new_video.is_absolute():
        new_video = ROOT / new_video
    video_str = str(new_video.resolve())

    # 1) 替换所有 clip 的素材文件；时间码保留（同结构素材复用）
    clips = sheet.get("clips") or []
    if not clips:
        raise ValueError(f"存档 {archive_path.name} 的模板里没有 clips，无法 restore")
    for c in clips:
        c["file"] = video_str

    # 2) 替换字幕/转写路径
    new_transcript = Path(new_transcript)
    if not new_transcript.is_absolute():
        new_transcript = ROOT / new_transcript
    sheet["subtitles_json"] = _to_root_rel(new_transcript)

    # 3) 更新工程名
    if not project:
        project = f"{new_video.stem}-成片"
    sheet["project"] = project

    # 4) 自检字段是针对旧素材算的，清掉避免误导
    sheet["自检"] = {
        "通过": None,
        "问题": ["restore 生成，未重新自检；建议跑 build_draft 前用 check_quality.py 复核"],
        "来源存档": archive_doc.get("id"),
    }

    output_sheet = Path(output_sheet)
    if not output_sheet.is_absolute():
        output_sheet = ROOT / output_sheet
    _write_json(output_sheet, sheet)
    return output_sheet


def list_archives() -> list[Path]:
    """扫描 ARCHIVE_DIR 下所有 .json 存档，按文件名排序。"""
    if not ARCHIVE_DIR.exists():
        return []
    return sorted(p for p in ARCHIVE_DIR.glob("*.json") if p.is_file())


def show_archive(archive_path: str | Path) -> dict:
    """读一个存档并返回可读摘要（同时用于 CLI 打印与自测）。"""
    archive_path = Path(archive_path)
    if not archive_path.is_absolute():
        archive_path = ROOT / archive_path
    doc = _read_json(archive_path)
    summary = {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "description": doc.get("description"),
        "version": doc.get("version"),
        "created": doc.get("created"),
        "tags": doc.get("tags"),
        "style_id": doc.get("style_id"),
        "template": doc.get("template"),
        "structure": doc.get("structure"),
        "parameters": doc.get("parameters"),
        "quality_gates": doc.get("quality_gates"),
        "source_sheet": doc.get("source_sheet"),
        "usage_example": doc.get("usage_example"),
    }
    return summary


# ---------- CLI ----------

def _cmd_archive(a: argparse.Namespace) -> int:
    tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()]
    out = archive(a.sheet, name=a.name, sid=a.id,
                  description=a.description or "", tags=tags)
    print(f"[archive] 已生成存档：{out}")
    print(json.dumps(show_archive(out), ensure_ascii=False, indent=2))
    return 0


def _cmd_restore(a: argparse.Namespace) -> int:
    out = restore(a.archive, a.video, a.transcript, a.out, project=a.project)
    print(f"[restore] 已生成新决策单：{out}")
    return 0


def _cmd_list(_a: argparse.Namespace) -> int:
    items = list_archives()
    if not items:
        print(f"[list] {ARCHIVE_DIR} 下暂无存档")
        return 0
    print(f"[list] 共 {len(items)} 个存档：")
    for p in items:
        try:
            d = _read_json(p)
            print(f"  - {d.get('id')}｜{d.get('name')}｜tags={','.join(d.get('tags') or [])}｜{p.name}")
        except Exception as e:  # noqa: BLE001
            print(f"  - {p.name}（读取失败：{e}）")
    return 0


def _cmd_show(a: argparse.Namespace) -> int:
    print(json.dumps(show_archive(a.archive), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="成片工作流存档：archive / restore / list / show")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("archive", help="从 sheet 生成存档")
    pa.add_argument("--sheet", required=True, help="03-scripts 下的 .sheet.json 路径")
    pa.add_argument("--id", required=True, help="存档 id，如 koubo-zhishi-v1")
    pa.add_argument("--name", required=True, help="存档名")
    pa.add_argument("--description", default="", help="可选描述")
    pa.add_argument("--tags", default="", help="逗号分隔标签")
    pa.set_defaults(func=_cmd_archive)

    pr = sub.add_parser("restore", help="换素材生成新 sheet")
    pr.add_argument("--archive", required=True, help="存档 JSON 路径")
    pr.add_argument("--video", required=True, help="新素材视频路径")
    pr.add_argument("--transcript", required=True, help="新转写/字幕 JSON 路径")
    pr.add_argument("--out", required=True, help="输出新 sheet 路径")
    pr.add_argument("--project", default=None, help="新工程名，默认 <素材名>-成片")
    pr.set_defaults(func=_cmd_restore)

    pl = sub.add_parser("list", help="列出所有存档")
    pl.set_defaults(func=_cmd_list)

    ps = sub.add_parser("show", help="显示存档详情")
    ps.add_argument("--archive", required=True)
    ps.set_defaults(func=_cmd_show)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
