#!/usr/bin/env python3
"""editor_bridges 全适配器集成自检。"""
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path
import sys
sys.path.insert(0, portable_path('project:lib'))

from editor_bridges.registry import list_software, get_bridge, detect_all, selftest_all

print("=== 已注册软件 ===")
for s in list_software():
    print(f"  {s['id']:15s} {s['name']}")

print()
print("=== 全适配器自检 ===")
results = selftest_all()
all_pass = True
for r in results:
    det = r.get("detect") or {}
    ok = r["roundtrip"] and r["validate"] and not r["errors"]
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  [{status}] {r['software_id']:15s} "
          f"installed={det.get('installed', False)} "
          f"avail={det.get('available', False)} "
          f"roundtrip={r['roundtrip']} "
          f"validate={r['validate']} "
          f"err={r['errors']}")

print()
print("=== 检测矩阵 ===")
for d in detect_all():
    print(f"  {d.software_id:15s} installed={d.installed} "
          f"running={d.running} available={d.available} | {d.detail[:70]}")

print()
if all_pass:
    print("ALL 7 ADAPTERS PASSED")
    sys.exit(0)
else:
    print("SOME ADAPTERS FAILED")
    sys.exit(1)
