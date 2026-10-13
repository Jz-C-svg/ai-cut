"""
smart_export 主入口脚本
用法：python scripts/smart_export.py <动作> [参数]
"""
import argparse
import json
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.smart_export import get_adapter, detect_all, get_available


def cmd_list():
    """列出所有支持软件"""
    results = get_available()
    print("=== 可用软件列表 ===")
    for r in results:
        print(f"[{r.software_id}] {r.software_name}")
        if r.running:
            print(f"  状态: 运行中")
        else:
            print(f"  状态: 未运行（需要启动软件）")
        print(f"  导出方式: {', '.join(m.name for m in r.supported_methods)}")
        print(f"  可用: {'是' if r.available else '否'}")
        print()


def cmd_detect():
    """检测所有软件状态"""
    results = detect_all()
    print("=== 软件检测报告 ===")
    print(f"{'软件':<20} {'状态':<12} {'MCP':<6} {'工程':<6} {'导出':<6}")
    print("-" * 60)
    for r in results:
        status = "运行中" if r.running else "未运行"
        mcp = "✓" if self._mcp_supported(r.software_id) else "×"
        project = "✓" if r.available else "×"
        export = "✓" if any(m.enabled for m in r.supported_methods) else "×"
        print(f"{r.software_name:<20} {status:<12} {mcp:<6} {project:<6} {export:<6}")
    print()


def cmd_export():
    """执行导出"""
    parser = argparse.ArgumentParser(description="智能导出")
    parser.add_argument("software", help="软件 ID (jianying/capcut/premiere等)")
    parser.add_argument("project", help="项目名称")
    parser.add_argument("--quality", default="high", choices=["high", "medium", "low"])
    parser.add_argument("--output-dir", help="导出目录")
    parser.add_argument("--format", default="mp4")
    args = parser.parse_args()

    try:
        adapter = get_adapter(args.software)
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)

    # 构建任务
    from lib.smart_export import ExportTask
    output_dir = Path(args.output_dir) if args.output_dir else Path(f"<project-root>/07-output")
    task = ExportTask(
        project_name=args.project,
        output_dir=output_dir,
        format=args.format,
        quality=args.quality,
        settings={},
        software_id=args.software
    )

    # 检测导出方式
    methods = adapter.detect_export_methods()
    optimal_method = adapter.choose_optimal_method(task, methods)

    print(f"=== 导出配置 ===")
    print(f"软件: {args.software} ({adapter.SOFTWARE_NAME})")
    print(f"项目: {args.project}")
    print(f"质量: {args.quality}")
    print(f"格式: {args.format}")
    print(f"导出方式: {optimal_method.name}")
    print(f"描述: {optimal_method.description}")
    print()

    if not optimal_method.enabled:
        print(f"错误: {optimal_method.name} 不可用")
        sys.exit(1)

    # 执行导出
    output_path = adapter.get_export_path(task)
    result = None

    if optimal_method.method_id == "ui":
        result = adapter.export_via_ui(args.project, settings={})
    elif optimal_method.method_id == "mcp":
        result = adapter.export_via_mcp(args.project, settings={})
    elif optimal_method.method_id == "project":
        result = adapter.export_via_project(args.project, output_path, settings={})
    else:
        print(f"未知导出方式: {optimal_method.method_id}")
        sys.exit(1)

    # 记录结果
    adapter.log_result(optimal_method.method_id, result)

    # 输出结果
    print(f"=== 导出结果 ===")
    print(f"状态: {'成功' if result.success else '失败'}")
    if result.success:
        print(f"输出路径: {result.output_path}")
        print(f"文件大小: {result.file_size_mb:.2f} MB")
        print(f"耗时: {result.duration:.2f} 秒")
    else:
        print(f"错误信息: {result.error_msg}")
    print()


def cmd_import_adapter():
    """导入新软件适配器"""
    parser = argparse.ArgumentParser(description="导入新软件适配器")
    parser.add_argument("software_id", help="软件 ID")
    parser.add_argument("module_path", help="适配器模块路径")
    parser.add_argument("software_name", help="软件显示名")
    parser.add_argument("--formats", nargs="+", help="支持的格式列表")
    args = parser.parse_args()

    from lib.smart_export.registry import _BRIDGE_REGISTRY, _SOFTWARE_NAMES

    _BRIDGE_REGISTRY[args.software_id] = args.module_path
    _SOFTWARE_NAMES[args.software_id] = args.software_name

    print(f"已添加适配器: {args.software_id} ({args.software_name})")
    print(f"模块路径: {args.module_path}")
    if args.formats:
        print(f"支持格式: {', '.join(args.formats)}")
    print()


def selftest():
    """自检"""
    from lib.smart_export import detect_all

    print("=== 智能导出模块自检 ===")
    results = detect_all()

    passed = sum(1 for r in results if r.available)
    total = len(results)

    print(f"测试进度: {passed}/{total}")
    for r in results:
        print(f"\n[{r.software_id}] {r.software_name}")
        print(f"  安装: {'是' if r.installed else '否'}")
        print(f"  运行: {'是' if r.running else '否'}")
        print(f"  MCP 支持: {self._mcp_supported(r.software_id)}")
        print(f"  工程可导出: {'是' if r.available else '否'}")
        if r.available:
            print("  ✅ 可用")
        else:
            print("  ❌ 不可用")

    print(f"\n自检结果: {passed}/{total} 通过")
    return passed == total


def _mcp_supported(software_id: str) -> bool:
    """检查软件是否支持 MCP"""
    if software_id == "jianying":
        try:
            import jianying_mcp
            return True
        except ImportError:
            return False
    return False


def main():
    parser = argparse.ArgumentParser(description="智能导出适配器 CLI")
    subparsers = parser.add_subparsers(dest="action", help="操作类型")

    # list
    subparsers.add_parser("list", help="列出支持软件")

    # detect
    subparsers.add_parser("detect", help="检测所有软件状态")

    # export
    export_parser = subparsers.add_parser("export", help="执行导出")
    export_parser.add_argument("software")
    export_parser.add_argument("project")
    export_parser.add_argument("--quality", default="high")
    export_parser.add_argument("--output-dir")
    export_parser.add_argument("--format", default="mp4")

    # import
    import_parser = subparsers.add_parser("import", help="导入新软件适配器")
    import_parser.add_argument("software_id")
    import_parser.add_argument("module_path")
    import_parser.add_argument("software_name")
    import_parser.add_argument("--formats", nargs="+")

    # selftest
    subparsers.add_parser("selftest", help="自检模块")

    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        sys.exit(1)

    if args.action == "list":
        cmd_list()
    elif args.action == "detect":
        cmd_detect()
    elif args.action == "export":
        cmd_export()
    elif args.action == "import":
        cmd_import_adapter()
    elif args.action == "selftest":
        passed = selftest()
        sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
