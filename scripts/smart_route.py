# -*- coding: utf-8 -*-
"""
smart_route.py — 平台-风格智能路由
输入：内容类型 / 目标平台 / 素材特征
输出：推荐风格卡 + 平台参数 + 智能调整建议
用法：
  python smart_route.py --content "口播知识讲解" --platform douyin
  python smart_route.py --content "产品对比测评" --platform bilibili
"""
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path
import argparse, json, os

ROOT = portable_path('project:')
ROUTER = os.path.join(ROOT, "content-library", "platform_style_router.json")
STYLES = os.path.join(ROOT, "content-library", "styles")


def load_router():
    with open(ROUTER, encoding="utf-8") as f:
        return json.load(f)


def match_content(router, content):
    table = router.get("内容类型→风格智能匹配") or router.get("内容类型→风格")
    # 精确/包含匹配
    for k, v in table.items():
        if k in content or content in k:
            return v
    # 关键词模糊
    if any(w in content for w in ["电影", "高燃", "情绪", "质感", "蒙太奇", "叙事"]):
        return "dianying-jiezou"
    if any(w in content for w in ["口播", "知识", "科普", "讲解"]):
        return "koubo-zhishi"
    if any(w in content for w in ["对比", "测评", "评测"]):
        return "duibi-ceping"
    if any(w in content for w in ["教程", "步骤", "教学"]):
        return "jiaocheng-buzhou"
    if any(w in content for w in ["vlog", "日常", "生活"]):
        return "jiating-richang"
    if any(w in content for w in ["卡点", "音乐", "炫技"]):
        return "kadian-jinji"
    return "koubo-zhishi"


def load_style(style_id):
    p = os.path.join(STYLES, style_id + ".json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def route(content, platform):
    router = load_router()
    style_id = match_content(router, content)
    plat = router["平台特性矩阵"].get(platform, {})
    style = load_style(style_id)
    return {
        "推荐风格": style_id + ("（" + style.get("名称", "") + "）" if style else ""),
        "风格描述": (style or {}).get("描述", ""),
        "平台": plat.get("名称", platform),
        "完播甜点": plat.get("完播甜点", ""),
        "节奏偏好": plat.get("节奏偏好", ""),
        "首选风格列表": plat.get("首选风格", []),
        "字幕策略": plat.get("字幕策略", ""),
        "平台参数调整": plat.get("参数调整", {}),
        "智能调整规则": router["智能调整规则"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True)
    ap.add_argument("--platform", default="douyin")
    args = ap.parse_args()
    result = route(args.content, args.platform)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
