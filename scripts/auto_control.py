r"""多剪辑软件自动操作 CLI（官方接口优先 + UI 自动化兜底）。

用法:
  python scripts/auto_control.py --software aftereffects --action cut
  python scripts/auto_control.py --software jianying --action cut --keys "ctrl+b,delete"
  python scripts/auto_control.py --software premiere --action cut,undo --batch
  python scripts/auto_control.py --list
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import auto_control  # noqa: E402
from editor_bridges import registry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="AI cut 自动操作（多剪辑软件）")
    ap.add_argument("--software", help="软件 id：jianying/capcut/premiere/davinci/finalcut/vegas/aftereffects")
    ap.add_argument("--action", help="操作：cut/delete/play/stop/undo/save（逗号分隔可批量）")
    ap.add_argument("--keys", help="UI 自动化快捷键序列（可选，如 'ctrl+b,delete'）")
    ap.add_argument("--prefer", choices=["auto", "official", "ui"], default="auto",
                    help="通道偏好：auto=官方接口优先（默认）/ official / ui")
    ap.add_argument("--batch", action="store_true", help="批量模式：--action 逗号分隔依次执行")
    ap.add_argument("--dry-run", action="store_true", help="只演练不实际执行")
    ap.add_argument("--pid", type=int, help="只操作指定进程")
    ap.add_argument("--list", action="store_true", help="列出支持的动作与通道")
    args = ap.parse_args()

    if args.list:
        print("=== 已注册软件与通道 ===")
        for sw in registry.list_software():
            bridge = registry.get_bridge(sw["id"])
            det = bridge.detect()
            print(f"  {sw['id']:<14} {sw['name']:<18} 安装={det.installed} 运行={det.running} "
                  f"可用={det.available}  动作: cut/delete/play/stop/undo/save")
        return 0

    if not args.software or not args.action:
        ap.error("需要 --software 与 --action（或 --list）")
    kw: dict = {"prefer": args.prefer, "dry_run": args.dry_run}
    if args.keys:
        kw["keys"] = args.keys
    if args.pid:
        kw["pid"] = args.pid

    actions = [a.strip() for a in args.action.split(",") if a.strip()]
    results = auto_control.execute_batch(args.software, actions, **kw)
    for r in results:
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    failed = [r for r in results if not r.success]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} 条成功 ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
