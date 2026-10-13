# -*- coding: utf-8 -*-
"""自测：skill_archive 的 archive / restore / list / show 与 skills.load_archive 接入。

运行：
    cd <project-root>
    $env:PYTHONPATH="<project-root>\\lib"
    venv\\Scripts\\python.exe scripts\\selftest_skill_archive.py

说明：
  - 用临时存档 id "_selftest-koubo-tmp-v1"，测完自动删除，不污染正式存档库。
  - 不跑 build_draft（重），只校验 restore 出的 sheet 字段齐全、可被 build_draft 消费。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import skill_archive as sa  # noqa: E402
from aicut import skills  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))


SRC_SHEET = ROOT / "03-scripts" / "IMG_5840-成片.sheet.json"
TEST_ID = "_selftest-koubo-tmp-v1"
TEST_ARCHIVE = sa.ARCHIVE_DIR / f"{TEST_ID}.json"
CONFLICT_ID = "delete_range"  # 一个内置技能 id，用来验证"冲突不覆盖"
CONFLICT_ARCHIVE = sa.ARCHIVE_DIR / f"{CONFLICT_ID}.json"
TEST_OUT_SHEET = ROOT / "work" / "selftest-restore.sheet.json"


def main() -> int:
    # 清理可能残留
    for f in (TEST_ARCHIVE, CONFLICT_ARCHIVE, TEST_OUT_SHEET):
        try:
            f.unlink()
        except OSError:
            pass

    # ---------- 1. archive ----------
    try:
        out = sa.archive(SRC_SHEET, name="自测临时存档", sid=TEST_ID,
                         description="自测用", tags=["自测"])
        doc = json.loads(out.read_text(encoding="utf-8"))
        check("archive 生成合法 JSON 文件", out.exists() and doc.get("id") == TEST_ID)
        check("archive 含结构/参数/验收规则",
              all(k in doc for k in ("parameters", "quality_gates", "sheet_template", "structure", "hooks")))
        check("archive 内嵌 sheet_template", "clips" in doc.get("sheet_template", {}))
    except Exception as e:  # noqa: BLE001
        check("archive 生成合法 JSON 文件", False, repr(e))

    # ---------- 2. restore ----------
    try:
        sa.restore(TEST_ARCHIVE,
                   "work/normalized/IMG_5841.mp4",
                   "02-transcript/IMG_5841-成片.auto.json",
                   TEST_OUT_SHEET)
        ns = json.loads(TEST_OUT_SHEET.read_text(encoding="utf-8"))
        files = {c["file"] for c in ns.get("clips", [])}
        check("restore 替换所有 clip 素材路径",
              len(files) == 1 and "IMG_5841.mp4" in next(iter(files)), str(files))
        check("restore 替换字幕路径", "IMG_5841-成片.auto.json" in ns.get("subtitles_json", ""),
              ns.get("subtitles_json", ""))
        check("restore 保留时间码（start/end/speed）",
              all("start" in c and "end" in c and "speed" in c for c in ns["clips"]))
        # build_draft 需要的关键字段
        need = {"project", "template", "clips", "subtitles_json", "width", "height", "fps"}
        check("restore 出的 sheet 具备 build_draft 消费字段",
              need.issubset(ns.keys()), f"缺 {need - ns.keys()}")
    except Exception as e:  # noqa: BLE001
        check("restore 替换素材路径", False, repr(e))

    # ---------- 3. list / show ----------
    try:
        items = sa.list_archives()
        check("list_archives 能列出测试存档",
              any(p.name == f"{TEST_ID}.json" for p in items), f"共 {len(items)} 个")
        summary = sa.show_archive(TEST_ARCHIVE)
        check("show_archive 返回可读摘要",
              summary.get("id") == TEST_ID and "parameters" in summary and "quality_gates" in summary)
    except Exception as e:  # noqa: BLE001
        check("list/show 正常", False, repr(e))

    # ---------- 4. skills 体系接入 ----------
    try:
        sk = skills.load_archive(TEST_ARCHIVE)
        check("load_archive 返回 Skill 对象",
              sk.id == TEST_ID and sk.channel == "shell" and callable(sk.fn))
        check("Skill.required 含 video/transcript/out",
              set(["video", "transcript", "out"]).issubset(set(sk.required)))
    except Exception as e:  # noqa: BLE001
        check("load_archive 返回 Skill 对象", False, repr(e))

    # 4b. 冲突保护：造一个 id = delete_range（内置技能）的存档，load_all_archives 不得覆盖它
    try:
        orig_channel = skills.REGISTRY["delete_range"].channel
        fake = json.loads(TEST_ARCHIVE.read_text(encoding="utf-8"))
        fake["id"] = CONFLICT_ID
        CONFLICT_ARCHIVE.write_text(json.dumps(fake, ensure_ascii=False), encoding="utf-8")
        before = len(skills.REGISTRY)
        skills.load_all_archives(register_it=True)
        after = len(skills.REGISTRY)
        still_builtin = skills.REGISTRY["delete_range"].channel == orig_channel
        check("id 冲突时不覆盖内置技能", still_builtin,
              f"delete_range.channel={skills.REGISTRY['delete_range'].channel}(应={orig_channel})")
    except Exception as e:  # noqa: BLE001
        check("id 冲突保护", False, repr(e))

    # ---------- 清理 ----------
    for f in (TEST_ARCHIVE, CONFLICT_ARCHIVE, TEST_OUT_SHEET):
        try:
            f.unlink()
        except OSError:
            pass

    # ---------- 汇总 ----------
    print("\n================ 自测结果 ================")
    print(f"PASS: {len(PASS)} 项")
    for n in PASS:
        print(f"  ✓ {n}")
    if FAIL:
        print(f"FAIL: {len(FAIL)} 项")
        for n in FAIL:
            print(f"  ✗ {n}")
    else:
        print("FAIL: 0 项")
    print("=========================================")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
