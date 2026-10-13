"""
smart_export 快速验证脚本
测试基础功能是否正常
"""
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path
import sys
import os
from pathlib import Path

# 确保项目根目录在路径中
project_root = Path(portable_path('project:'))
sys.path.insert(0, str(project_root))

# 导入模块
from lib.smart_export.registry import detect_all, list_software, get_adapter
from lib.smart_export.jianying import JianyingExportAdapter
print(f"项目根: {project_root}")


def quick_test():
    """快速测试"""
    print("=== 智能导出模块快速测试 ===\n")

    # 1. 测试列出软件
    print("1. 列出所有支持的软件...")
    software_list = list_software()
    print(f"   发现 {len(software_list)} 个软件:")
    for s in software_list:
        print(f"   - {s['id']}: {s['name']}")
    print()

    # 2. 测试检测
    print("2. 检测软件状态...")
    results = detect_all()
    print(f"   共检测 {len(results)} 个软件:")
    for r in results:
        available_str = "✓ 可用" if r.available else "✗ 不可用"
        print(f"   - [{r.software_id}] {r.software_name}: {available_str}")
    print()

    # 3. 测试剪映适配器
    print("3. 测试剪映适配器...")
    try:
        adapter = JianyingExportAdapter()
        detection = adapter.detect()
        print(f"   安装: {detection.installed}")
        print(f"   运行: {detection.running}")
        print(f"   MCP 支持: {adapter.mcp_configured}")
        print(f"   草稿存在: {'是' if detection.available else '否'}")
        print(f"   支持方法: {len(detection.supported_methods)}")
        for method in detection.supported_methods:
            print(f"     - {method.name} ({method.method_id}): enabled={method.enabled}, {method.description}")
    except Exception as e:
        print(f"   错误: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    print()

    print("=== 测试完成 ===")


def _check_mcp(adapter):
    """检查 MCP 支持"""
    try:
        import jianying_mcp
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    quick_test()
