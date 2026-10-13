#!/usr/bin/env python3
r"""v3.1 资源 ID 注册表自测。

不依赖剪映是否开着。校验：JSON 合法、每条都有来源、查询/反查/统计正确。

用法:
  python scripts/selftest_resource_registry.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut.paths import CONFIG_DIR  # noqa: E402
from aicut.resource_registry import (  # noqa: E402
    REQUIRED_CATEGORIES, ResourceRegistry,
)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    reg_path = CONFIG_DIR / "resource_registry.json"

    # 1. 注册表文件存在且 JSON 合法
    check("注册表：文件存在且为合法 JSON",
          reg_path.exists(), str(reg_path))
    raw = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {}
    check("注册表：顶层含 entries 数组", isinstance(raw.get("entries"), list),
          f"entries={len(raw.get('entries', []))}")

    reg = ResourceRegistry.load()
    total = len(reg.entries)

    # 2. 每个条目都有 source（无来源条目不存在）
    no_src = [e for e in reg.entries.values()
              if not e.get("source") or (isinstance(e["source"], list) and not e["source"])]
    check("诚实性：无任何条目缺 source 字段", len(no_src) == 0,
          f"缺来源条目 {len(no_src)} 个")

    # 2b. 每条 resource_id 都非空（无编造空 ID）
    empty_rid = [e for e in reg.entries.values() if not str(e.get("resource_id"))]
    check("诚实性：无空/编造的 resource_id", len(empty_rid) == 0,
          f"空 rid {len(empty_rid)} 个")

    # 3. query 按类别 / 关键词过滤正确
    fonts_all = reg.query("fonts")
    check("查询：按类别过滤只返回该类别",
          fonts_all and all(e["category"] == "fonts" for e in fonts_all),
          f"fonts {len(fonts_all)} 条")
    kw = reg.query("fonts", keyword="明朝")
    check("查询：关键词命中名字包含项",
          all("明朝" in e["name"] for e in kw) and len(kw) >= 1,
          f"命中 {len(kw)} 条: {[e['name'] for e in kw][:3]}")
    wrong_kw = reg.query("fonts", keyword="绝对不可能存在的名字xyz")
    check("查询：无关键词命中时返回空", wrong_kw == [], f"命中 {len(wrong_kw)}")

    # 4. get_by_resource_id 能查到已知 ID
    sample = reg.query("fonts")[0]
    found = reg.get_by_resource_id(sample["resource_id"])
    check("反查：按 resource_id 命中已知条目",
          found is not None and found["resource_id"] == sample["resource_id"],
          f"rid={sample['resource_id']} -> {found['name'] if found else None}")
    miss = reg.get_by_resource_id("9999999999999999999")
    check("反查：不存在的 ID 返回 None", miss is None)

    # 5. stats 返回各类别数量且总数 > 0
    st = reg.stats()
    check("统计：total>0 且与实际条目数一致",
          st["total"] == total and total > 0, f"total={total}")
    check("统计：7 个主类别都在统计里",
          all(c in st["required_categories"] for c in REQUIRED_CATEGORIES),
          str(st["required_categories"]))

    # 6. add_resource 增量写入 + 缺 source 被拒绝
    reg2 = ResourceRegistry()  # 内存空表
    reg2.add_resource({
        "id": "text_styles:TEST001", "name": "测试花字", "category": "text_styles",
        "resource_id": "TEST001", "effect_id": "e1", "source": ["draft:测试"],
        "verified_at": "2026-09-11", "notes": "自测",
    })
    check("增量：add_resource 可写入新类别样例",
          reg2.get_by_resource_id("TEST001") is not None)
    rejected = False
    try:
        reg2.add_resource({"id": "x:1", "name": "", "category": "fonts",
                           "resource_id": "1", "effect_id": "", "source": [],
                           "verified_at": "", "notes": ""})
    except ValueError:
        rejected = True
    check("防编造：无 source 的条目被拒绝", rejected)

    # 7. 缺口诚实性：未收录类别如实为 0，且披露信息存在
    disc = raw.get("disclosure", {})
    check("缺口说明：含 scope_note 与 claimed 规模披露",
          bool(disc.get("scope_note")) and bool(disc.get("claimed")) and bool(disc.get("why_gap")),
          disc.get("scope_note", ""))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
