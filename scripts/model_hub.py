"""
AI Cut 模型集成工具 v1.0
统一调用接口，支持：
- GLM-4V 视觉理解
- GLM-4 文本决策
- OpenRouter 各种模型
- Ollama 本地模型

用法：
  python model_hub.py vision 图片路径 "问题" --model glm-4v-flash
  python model_hub.py text "提示词" --model glm-4-flash
  python model_hub.py list
"""
import json, os, sys, base64, urllib.request, glob
from pathlib import Path
from PIL import Image

# ============ 配置 ============
CONFIG = {
    "glm": {
        "api_key": os.environ.get('AICUT_GLM_API_KEY', ""),
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": {
            "glm-4v-flash": {"type": "vision", "max_tokens": 500},
            "glm-4v-plus": {"type": "vision", "max_tokens": 1000},
            "glm-4-flash": {"type": "text", "max_tokens": 2000},
            "glm-4-plus": {"type": "text", "max_tokens": 4000},
        }
    },
    "openrouter": {
        "api_key": os.environ.get('AICUT_OPENROUTER_API_KEY', ""),
        "base_url": "https://openrouter.ai/api/v1",
        "models": {
            "gpt-4o": {"type": "vision"},
            "claude-3.5-sonnet": {"type": "vision"},
            "gemini-2.0-flash-exp": {"type": "vision"},
            "deepseek-chat": {"type": "text"},
            "qwen-2.5-72b": {"type": "text"},
        }
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434",
        "models": {
            "llama3.1:8b": {"type": "text"},
            "llava:7b": {"type": "vision"},
            "qwen2.5vl:7b": {"type": "vision"},
        }
    }
}

# ============ 核心调用 ============
def call_glm(model, prompt, image=None):
    """调用智谱GLM"""
    cfg = CONFIG["glm"]
    url = f"{cfg['base_url']}/chat/completions"

    if image and cfg["models"][model]["type"] == "vision":
        # 视觉模型
        img = Image.open(image)
        img.thumbnail((512, 512))
        tmp = str(image) + ".tmp.jpg"
        img.save(tmp, quality=70)
        with open(tmp, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        os.remove(tmp)

        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]
    else:
        content = prompt

    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": cfg["models"][model].get("max_tokens", 1000),
        "temperature": 0.5
    }).encode()

    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json"
    })
    r = urllib.request.urlopen(req, timeout=120)
    resp = json.loads(r.read())
    return resp["choices"][0]["message"]["content"]

def call_openrouter(model, prompt, image=None):
    """调用OpenRouter"""
    cfg = CONFIG["openrouter"]
    url = f"{cfg['base_url']}/chat/completions"

    if image:
        img = Image.open(image)
        img.thumbnail((512, 512))
        tmp = str(image) + ".tmp.jpg"
        img.save(tmp, quality=70)
        with open(tmp, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        os.remove(tmp)
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]
    else:
        content = prompt

    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 2000,
        "temperature": 0.5
    }).encode()

    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://aicut.local",
        "X-Title": "AI Cut"
    })
    r = urllib.request.urlopen(req, timeout=120)
    resp = json.loads(r.read())
    return resp["choices"][0]["message"]["content"]

def call_ollama(model, prompt, image=None):
    """调用本地Ollama"""
    cfg = CONFIG["ollama"]
    url = f"{cfg['base_url']}/api/chat"

    if image:
        img = Image.open(image)
        img.thumbnail((512, 512))
        tmp = str(image) + ".tmp.jpg"
        img.save(tmp, quality=70)
        with open(tmp, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        os.remove(tmp)
        data = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": [b64]}],
            "stream": False,
            "think": False
        }).encode()
    else:
        data = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False
        }).encode()

    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json"
    })
    r = urllib.request.urlopen(req, timeout=120)
    resp = json.loads(r.read())
    return resp["message"]["content"]

# ============ 统一入口 ============
def call_model(model, prompt, image=None):
    """统一调用：自动识别模型来源"""
    # 识别模型来源
    if model.startswith("glm"):
        return call_glm(model, prompt, image)
    elif model in ["gpt-4o", "claude-3.5-sonnet", "gemini-2.0-flash-exp", "deepseek-chat", "qwen-2.5-72b"]:
        return call_openrouter(model, prompt, image)
    else:
        return call_ollama(model, prompt, image)

def list_models():
    """列出所有可用模型"""
    print("=== GLM (智谱) ===")
    for name, cfg in CONFIG["glm"]["models"].items():
        print(f"  {name:<20} {cfg['type']}")

    print("\n=== OpenRouter ===")
    for name, cfg in CONFIG["openrouter"]["models"].items():
        print(f"  {name:<25} {cfg['type']}")

    print("\n=== Ollama (本地) ===")
    for name, cfg in CONFIG["ollama"]["models"].items():
        print(f"  {name:<20} {cfg['type']}")

# ============ 命令行 ============
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        list_models()

    elif cmd == "vision":
        # python model_hub.py vision 图片路径 "问题" --model glm-4v-flash
        img = sys.argv[2]
        q = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "描述这张图"
        model = "glm-4v-flash"
        for i, a in enumerate(sys.argv):
            if a == "--model" and i+1 < len(sys.argv):
                model = sys.argv[i+1]
        print(f"模型: {model}")
        print(call_model(model, q, img))

    elif cmd == "text":
        # python model_hub.py text "提示词" --model glm-4-flash
        prompt = sys.argv[2]
        model = "glm-4-flash"
        for i, a in enumerate(sys.argv):
            if a == "--model" and i+1 < len(sys.argv):
                model = sys.argv[i+1]
        print(f"模型: {model}")
        print(call_model(model, prompt))

    elif cmd == "test":
        # python model_hub.py test
        print("测试模型调用...")
        try:
            result = call_model("glm-4-flash", "你好，说一句话")
            print(f"GLM测试: {result[:100]}")
        except Exception as e:
            print(f"GLM失败: {e}")

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
