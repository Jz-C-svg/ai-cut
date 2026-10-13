"""
AI Cut 全流程打通大脑
GLM-4V 视觉理解 → 匹配风格库 → 匹配架构库 → RAG知识库检索 → 自动生成剪辑方案
"""
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path
import json, os, glob
from pathlib import Path

ROOT = Path(portable_path('project:'))
LIB = ROOT / "content-library"

class BrainEngine:
    def __init__(self):
        self.styles = self._load_json_dir(LIB / "styles")
        self.architectures = self._load_json_dir(LIB / "architectures")
        self.skills = self._load_json_dir(LIB / "skills")
        self.visuals = self._load_visuals()
        self.rag_knowledge = self._load_rag()
        print(f"大脑加载完成：{len(self.styles)}风格, {len(self.architectures)}架构, {len(self.skills)}技能, {len(self.visuals)}视觉素材, {len(self.rag_knowledge)}条RAG知识")

    def _load_json_dir(self, path):
        result = {}
        if not path.exists():
            return result
        for f in path.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                result[data.get("id", f.stem)] = data
            except:
                pass
        return result

    def _load_visuals(self):
        manifest = LIB / "visuals" / "manifest.json"
        if manifest.exists():
            return json.loads(manifest.read_text(encoding="utf-8"))
        return {}

    def _load_rag(self):
        """加载RAG知识库"""
        rag_file = LIB / "rag" / "viral_short_video_rag.jsonl"
        knowledge = []
        if rag_file.exists():
            for line in rag_file.read_text(encoding="utf-8").strip().split("\n"):
                if line.strip():
                    try:
                        knowledge.append(json.loads(line))
                    except:
                        pass
        return knowledge

    def rag_search(self, query, top_k=5):
        """RAG检索：根据查询返回最相关的知识"""
        query_lower = query.lower()
        scores = []
        for item in self.rag_knowledge:
            score = 0
            # 匹配关键词
            for kw in item.get("keywords", []):
                if kw.lower() in query_lower:
                    score += 3
            # 匹配搜索词
            for term in item.get("search_terms", []):
                if term.lower() in query_lower:
                    score += 2
            # 匹配名称
            if item.get("name", "").lower() in query_lower:
                score += 5
            if score > 0:
                scores.append((score, item))
        scores.sort(reverse=True, key=lambda x: x[0])
        return [item for _, item in scores[:top_k]]

    def get_platform_knowledge(self, platform_name):
        """获取特定平台的知识"""
        for item in self.rag_knowledge:
            if item.get("category") == "platform" and platform_name.lower() in item.get("name", "").lower():
                return item
        return None

    def get_style_knowledge(self, style_name):
        """获取特定风格的知识"""
        for item in self.rag_knowledge:
            if item.get("category") == "style" and style_name.lower() in item.get("name", "").lower():
                return item
        return None

    def match_style(self, vision_analysis):
        """根据视觉分析匹配最合适的风格"""
        all_text = " ".join([d for _, d in vision_analysis]).lower()
        scores = []
        for sid, style in self.styles.items():
            score = 0
            # 匹配适用素材特征
            features = style.get("适用素材特征", {})
            for kw in features.get("内容类型", []):
                if kw.lower() in all_text:
                    score += 3
            for kw in features.get("情绪基调", []):
                if kw.lower() in all_text:
                    score += 2
            for kw in features.get("场景类型", []):
                if kw.lower() in all_text:
                    score += 2
            for kw in features.get("主角类型", []):
                if kw.lower() in all_text:
                    score += 1
            scores.append((score, sid, style))
        scores.sort(reverse=True)
        return scores[0][2] if scores else None

    def match_architecture(self, style):
        """根据风格匹配最合适的架构"""
        style_id = style.get("id", "")
        # 直接匹配
        if style_id in self.architectures:
            return self.architectures[style_id]
        # 模糊匹配
        for aid, arch in self.architectures.items():
            if style_id in aid or aid in style_id:
                return arch
        # 默认vlog
        return self.architectures.get("vlog-richang", list(self.architectures.values())[0])

    def generate_plan(self, vision_analysis, video_clips):
        """根据视觉分析+风格+架构+RAG知识生成完整剪辑方案"""
        # 1. 匹配风格
        style = self.match_style(vision_analysis)
        style_name = style.get("名称", "默认风格") if style else "默认"
        print(f"匹配风格: {style_name}")

        # 2. 匹配架构
        arch = self.match_architecture(style)
        arch_name = arch.get("名称", "默认架构") if arch else "默认"
        print(f"匹配架构: {arch_name}")

        # 3. RAG检索增强
        all_text = " ".join([d for _, d in vision_analysis])
        rag_results = self.rag_search(all_text + " " + style_name, top_k=3)
        print(f"RAG检索到 {len(rag_results)} 条相关知识")

        # 4. 根据架构+RAG生成剪辑方案
        plan = {
            "风格": style_name,
            "架构": arch_name,
            "滤镜": style.get("视觉风格", {}).get("推荐滤镜", "奶油白") if style else "奶油白",
            "滤镜强度": style.get("视觉风格", {}).get("滤镜强度", 55) if style else 55,
            "转场": style.get("转场偏好", {}).get("默认转场", "叠化") if style else "叠化",
            "转场时长": style.get("转场偏好", {}).get("默认时长", 0.8) if style else 0.8,
            "花字": self._get_text_overlays(style, len(video_clips)),
            "节奏": style.get("节奏偏好", "舒缓") if style else "舒缓",
            "视觉素材": self._get_visual_suggestions(style),
            "RAG参考": [item.get("name", "") for item in rag_results]
        }

        # 5. 用RAG知识优化参数
        for rag_item in rag_results:
            if rag_item.get("category") == "style":
                features = rag_item.get("features", {})
                if "duration" in features:
                    plan["推荐时长"] = features["duration"]
                if "bgm" in features:
                    plan["BGM建议"] = features["bgm"]
                if "transition" in features:
                    plan["转场偏好"] = features["transition"]

        return plan

    def _get_text_overlays(self, style, clip_count):
        """根据风格生成花字方案"""
        texts = style.get("花字建议", [])
        if not texts:
            texts = ["记录美好瞬间", "爱与陪伴", "成长记录"]
        result = []
        for i in range(min(clip_count, len(texts))):
            result.append({
                "t0": i * 7 + 0.5,
                "t1": i * 7 + 3.5,
                "content": texts[i],
                "position": "center",
                "style": {"font_size": 11.0, "color": "#FF6B9D"}
            })
        return result

    def _get_visual_suggestions(self, style):
        """根据风格推荐视觉素材"""
        suggestions = []
        visuals = self.visuals.get("categories", {})
        for cat, items in visuals.items():
            if style and cat in str(style).lower():
                suggestions.extend(items[:3])
        return suggestions

    def build_decision_sheet(self, project_name, vision_analysis, clips, output_dir):
        """生成完整决策单"""
        plan = self.generate_plan(vision_analysis, clips)

        decision = {
            "project": project_name,
            "template": plan.get("风格", "默认"),
            "style": plan.get("风格", "默认"),
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "cover_text": plan.get("花字", [{}])[0].get("content", "记录美好"),
            "clips": clips,
            "画面": {
                "滤镜": plan.get("滤镜", "奶油白"),
                "滤镜强度": plan.get("滤镜强度", 55)
            },
            "字幕": {
                "字体": "抖音美好体",
                "字号": 8.0,
                "位置": "bottom"
            },
            "封面": {
                "字体": "站酷酷黑体",
                "字号": 11.0,
                "入场动画": "向上滑动"
            },
            "风格": plan.get("风格", "默认"),
            "结构": plan.get("架构", "默认"),
            "visual_plan": plan.get("花字", [])
        }

        out_path = Path(output_dir) / f"{project_name}.sheet.json"
        out_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"决策单已生成: {out_path}")
        return decision, out_path

# 测试
if __name__ == "__main__":
    brain = BrainEngine()
    # 模拟视觉分析结果
    test_analysis = [
        ("0s", "一家五口坐在沙发上，面前有蛋糕和红蛋，温馨家庭满月聚会"),
        ("120s", "小女孩穿着条纹衫，家人互动，幸福时光"),
        ("180s", "年轻女孩和一对男女，家庭合影")
    ]
    plan = brain.generate_plan(test_analysis, [])
    print("\n生成方案:")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
