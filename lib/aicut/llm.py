r"""模型客户端：云端优先，本地兜底。

分工沿用工程既有约定：
  · 想（规划、理解）  云端 deepseek-chat，快且 JSON 稳；失败或断网回退本地 Ollama
  · 看（画面描述）    本地视觉模型（Ollama 多模态），素材不出机器

调用点集中在这里，方便以后换模型、加限流、记账。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from .ollama_endpoint import ollama_url

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
OLLAMA_URL = ollama_url('/api/chat')
OLLAMA_MODEL = "gemma4:e2b"

# ---------- v4.0.0 P0：多接口 AI 大脑路由层（可选接入，向后兼容） ----------
# 配置了 ai_backends 时，chat/vision_json 的 auto 模式委托给 llm_router 的降级链；
# 未配置或委托全部失败时，回退到下面 v3.9 的原有逻辑，行为不变。
try:
    from .llm_router import llm_chat as _router_chat, router_enabled as _router_enabled
except Exception:  # noqa: BLE001  —— 路由层不可用时全部走原逻辑
    _router_chat = None
    _router_enabled = lambda: False  # noqa: E731

# ---------- v3.9.0：本地视觉模型可切换（gemma4 多模态 / Qwen2.5-VL） ----------
# 别名 -> ollama 真实模型名。新增视觉模型时在这里登记即可。
VISION_MODELS = {"qwen3.8": "qwen3.8:27b", "gemma4": "gemma4:e2b"}
# This verified local checkpoint fits the current host; larger models are opt-in.
VISION_DEFAULT_FALLBACK = "gemma4:e2b"


def _ollama_tags() -> list[str]:
    """读 ollama /api/tags，返回已安装模型名列表；ollama 没起来时返回空列表。"""
    try:
        req = urllib.request.Request(ollama_url('/api/tags'))
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def get_vision_model() -> str:
    """返回当前默认本地视觉模型名。

    优先级：
      1. 环境变量 AICUT_VISION_MODEL（临时切换，最高优先级）
      2. config/aicut.json 的 vision.model（scripts/model_manager.py switch 写入）
      3. 自动探测：ollama 里装了 qwen2.5vl:* 就用它，否则回退 gemma4
    """
    env = os.environ.get("AICUT_VISION_MODEL", "").strip()
    if env:
        return env
    try:
        from .paths import config  # 局部导入，避免模块级循环依赖
        name = (config().get("vision") or {}).get("model")
        if isinstance(name, str) and name.strip():
            name = name.strip()
            installed = _ollama_tags()
            # ollama 在跑但配置的模型还没拉下来（如 qwen2.5vl 拉取中），不硬用，
            # 继续走自动探测；ollama 连不上时信任配置（远端/启动顺序场景）。
            if not installed or name in installed:
                return name
    except Exception:  # noqa: BLE001
        pass
    installed = _ollama_tags()
    if installed and not any(
        any(token in name.lower() for token in ("gemma4", "qwen", "llava", "minicpm", "vision"))
        for name in installed
    ):
        return ""
    wanted = VISION_MODELS["gemma4"]
    if wanted in installed:
        return wanted
    # 宽松匹配：可能是 gemma4:4b / 12b 等 tag 变体
    match = [n for n in installed if "gemma4" in n.lower()]
    if match:
        return match[0]
    return VISION_DEFAULT_FALLBACK

# v3.2-B1：显式约束上下文长度与生成长度。
#   num_ctx=32768  与质量决策通道使用同一上下文容量，避免视觉和规划反复重载。
#   num_predict=640 上限生成长度，防止模型啰嗦或不停生成拖慢单次响应（结构化 JSON 远用不到）。
OLLAMA_NUM_CTX = 32768
OLLAMA_NUM_PREDICT = 640


def _ollama_options(temperature: float) -> dict:
    return {"temperature": temperature, "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": OLLAMA_NUM_PREDICT}


CALL_LOG: list[dict] = []


def warmup_models(also_text: bool = True, timeout: int = 900) -> dict:
    """后台预热：把默认视觉 + 文本模型加载进内存并 keep_alive 保活。

    v5.0.0（P1 冷启动优化）：GUI 启动时在后台线程调用，把"首载等待"从用户
    剪辑时转移到程序启动时；任何异常不抛出（后台任务，失败不影响主流程）。
    返回 {"vision": 秒|"fail", "text": 秒|"fail"|"skip"}。
    """
    results: dict = {}
    vision_model = get_vision_model()
    try:
        t0 = time.time()
        _post(OLLAMA_URL, {
            "model": vision_model,
            "messages": [{"role": "user", "content": "回复：就绪"}],
            "stream": False, "think": False, "keep_alive": "30m",
            "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": 8},
        }, timeout=timeout)
        results["vision"] = round(time.time() - t0, 1)
    except Exception:  # noqa: BLE001
        results["vision"] = "fail"
    if also_text:
        try:
            from .llm_router import route_backends
            local = [b for b in route_backends('decision') if b.type == 'ollama']
            text_model = local[0].model if local else OLLAMA_MODEL
            if text_model == vision_model:
                results['text'] = 'shared_with_vision'
                return results
            t0 = time.time()
            _post(OLLAMA_URL, {
                "model": text_model,
                "messages": [{"role": "user", "content": "回复：就绪"}],
                "stream": False, "think": False, "keep_alive": "30m",
                "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": 8},
            }, timeout=timeout)
            results["text"] = round(time.time() - t0, 1)
        except Exception:  # noqa: BLE001
            results["text"] = "fail"
    else:
        results["text"] = "skip"
    return results


def api_key(name: str = "DEEPSEEK_API_KEY") -> str:
    key = os.environ.get(name)
    if key:
        return key
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"[Environment]::GetEnvironmentVariable('{name}','User')"],
            capture_output=True, text=True, timeout=30,
        )
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _post(url: str, payload: dict, headers: dict | None = None, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    from .inference_queue import inference_slot
    with inference_slot(url):
        from .ollama_transport import is_local_chat, post_stream
        if is_local_chat(url):
            return post_stream(url, payload, headers, timeout)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


def _deepseek(messages: list[dict], *, model: str, json_mode: bool, temperature: float,
              timeout: int) -> str:
    key = api_key()
    if not key:
        raise RuntimeError("没有 DEEPSEEK_API_KEY")
    payload: dict = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _post(DEEPSEEK_URL, payload, {"Authorization": f"Bearer {key}"}, timeout=timeout)
    return data["choices"][0]["message"]["content"]


def _ollama(messages: list[dict], *, model: str, json_mode: bool, temperature: float,
            timeout: int) -> str:
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",
        "think": False,  # v3.2：本机 gemma4 带思考模式，默认会吃光 num_predict 预算导致空返回；关闭后直接出答案
        "options": _ollama_options(temperature),
    }
    if json_mode:
        payload["format"] = "json"
    data = _post(OLLAMA_URL, payload, timeout=timeout)
    return data.get("message", {}).get("content", "")


def chat(messages: list[dict] | str, *, provider: str = "auto", model: str | None = None,
         json_mode: bool = False, temperature: float = 0.2, timeout: int = 600) -> str:
    """按 provider 顺序尝试；auto = 本地免费优先（Ollama），云端 DeepSeek 仅作备选（需充值）。

    v4.0.0：配置了 ai_backends 时，auto 模式先交给 llm_router 按降级链跑（付费 API 优先），
    路由全部失败再回退到本地原逻辑；显式 provider（如 "ollama"）不经过路由。
    v5.0.0：误传字符串时自动包装为单条用户消息（防御，避免 400）。
    """
    if isinstance(messages, str):  # 容错：str -> [{"role":"user","content":str}]
        messages = [{"role": "user", "content": messages}]
    if provider == "auto" and _router_chat is not None and _router_enabled():
        started = time.time()
        try:
            text = _router_chat(messages, task_type="text", json_mode=json_mode,
                                temperature=temperature, timeout=timeout)
            from . import llm_router as _lr
            CALL_LOG.append({"provider": f"router:{_lr.LAST_USED_BACKEND}",
                             "seconds": round(time.time() - started, 2), "ok": True})
            return text
        except Exception as exc:  # noqa: BLE001 —— 路由全挂，回退原链路兜底
            CALL_LOG.append({"provider": "router", "seconds": round(time.time() - started, 2),
                             "ok": False, "error": str(exc)[:200]})
    order = ["ollama", "deepseek"] if provider == "auto" else [provider]
    errors = []
    for prov in order:
        started = time.time()
        try:
            if prov == "deepseek":
                text = _deepseek(messages, model=model or DEEPSEEK_MODEL, json_mode=json_mode,
                                 temperature=temperature, timeout=timeout)
            elif prov == "ollama":
                text = _ollama(messages, model=model or OLLAMA_MODEL, json_mode=json_mode,
                               temperature=temperature, timeout=timeout)
            else:
                raise ValueError(f"未知 provider：{prov}")
            CALL_LOG.append({"provider": prov, "seconds": round(time.time() - started, 2), "ok": True})
            return text
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{prov}: {type(exc).__name__} {exc}")
            CALL_LOG.append({"provider": prov, "seconds": round(time.time() - started, 2),
                             "ok": False, "error": str(exc)[:200]})
    raise RuntimeError("模型都不可用 -> " + " | ".join(errors))


_JSON_RE = re.compile(r"\{.*\}", re.S)


def extract_json(text: str) -> dict:
    """模型偶尔带解释或 ```json 围栏，这里做一次稳健抽取。"""
    if not text:
        raise ValueError("模型返回空")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    m = _JSON_RE.search(cleaned)
    if not m:
        raise ValueError(f"没有找到 JSON：{text[:200]}")
    return json.loads(m.group(0))


def json_chat(messages: list[dict], **kw) -> dict:
    kw.setdefault("json_mode", True)
    return extract_json(chat(messages, **kw))


def vision_describe(image: str | Path, prompt: str, *, model: str | None = None,
                    timeout: int = 600) -> str:
    """本地视觉模型看图（画面不外传）。model 缺省时用 get_vision_model() 的当前默认。"""
    b64 = base64.b64encode(Path(image).read_bytes()).decode("ascii")
    return _ollama(
        [{"role": "user", "content": prompt, "images": [b64]}],
        model=model or get_vision_model(), json_mode=False, temperature=0.3, timeout=timeout,
    )


def last_calls() -> list[dict]:
    return CALL_LOG[-10:]


VISION_MODEL = "deepseek-v4-flash-vision-exp"


def _b64(path: str | Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def assert_local_vision_healthy(model: str) -> None:
    """Reject a checkpoint quarantined by a real-frame review, including multi-image calls."""
    if os.environ.get('AICUT_VISION_DIAGNOSTIC') == '1':
        return
    from .paths import WORK
    health_file = WORK / 'vision-model-health.json'
    if not health_file.exists():
        return
    health = json.loads(health_file.read_text(encoding='utf-8'))
    entry = health.get(model, {})
    if entry.get('status') == 'failed':
        import hashlib
        tags = _post(ollama_url('/api/show'), {'model': model}, timeout=10)
        identity = hashlib.sha256(str(tags.get('modelfile', '')).encode()).hexdigest()
        if identity == entry.get('identity'):
            raise RuntimeError('本地视觉模型未通过真实画面验收：' + str(entry.get('reason', '')))


def vision_json(image: str | Path, prompt: str, *, provider: str = "auto",
                model: str | None = None, max_tokens: int = 1500,
                timeout: int = 300, schema: dict | None = None) -> dict:
    """看图 + 结构化输出。云端优先（deepseek 视觉模型），失败回退本地 Ollama。

    与 understand.py 的 vision_cloud 用同一条链路（chat/completions + image_url 内联 base64），
    区别是这里要求模型**只输出 JSON**，并用 extract_json 做稳健解析。
    """
    if schema is not None:
        if provider != 'ollama':
            raise ValueError('Strict visual schema currently requires explicit local Ollama')
        prompt += '\n严格输出以下 JSON Schema，字段类型不得改变：\n' + json.dumps(schema, ensure_ascii=False)
    # v4.0.0：auto 模式优先走 router 的视觉降级链（支持视觉的付费 API → 本地 Ollama）；
    # 显式 provider（如 "ollama"，隐私模式）不经过路由；路由全挂回退原逻辑。
    if provider == "auto" and _router_chat is not None and _router_enabled():
        started = time.time()
        try:
            text = _router_chat([{"role": "user", "content": prompt}], task_type="vision",
                                images=[image], json_mode=True, temperature=0.3, timeout=timeout)
            out = extract_json(text)
            from . import llm_router as _lr
            src = _lr.LAST_USED_BACKEND or "router"
            CALL_LOG.append({"provider": f"router:{src}", "vision": True,
                             "seconds": round(time.time() - started, 2), "ok": True})
            out["_provider"] = src
            return out
        except Exception as exc:  # noqa: BLE001
            CALL_LOG.append({"provider": "router", "vision": True,
                             "seconds": round(time.time() - started, 2),
                             "ok": False, "error": str(exc)[:160]})
    order = ["ollama", "deepseek"] if provider == "auto" else [provider]
    errors: list[str] = []
    for prov in order:
        started = time.time()
        try:
            if prov == "deepseek":
                key = api_key()
                if not key:
                    raise RuntimeError("没有 DEEPSEEK_API_KEY")
                payload = {
                    "model": model or VISION_MODEL,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": "data:image/jpeg;base64," + _b64(image)}},
                    ]}],
                }
                data = _post(DEEPSEEK_URL, payload, {"Authorization": f"Bearer {key}"}, timeout=timeout)
                text = data["choices"][0]["message"]["content"]
            else:
                selected_model = model or get_vision_model()
                # Quarantine locally observed visual failures, keyed to model digest.
                # Diagnostic runs may bypass this explicitly to verify a repair.
                assert_local_vision_healthy(selected_model)
                payload = {
                    # v3.9.0：ollama 视觉分支默认用 get_vision_model()（gemma4 / qwen2.5vl 可切），
                    # 调用方显式传 model 时优先使用。think:false 对 qwen2.5vl 无副作用（非思考模型），保留。
                    "model": selected_model,
                    "messages": [{"role": "user", "images": [_b64(image)], "content": prompt}],
                    "stream": False,
                    "keep_alive": "30m",
                    "format": schema if schema is not None else "json",
                    "think": False,  # 同 _ollama：关闭思考模式，避免吃光 num_predict 导致空返回
                    "options": {**_ollama_options(0.3), "num_predict": max_tokens},
                }
                data = _post(OLLAMA_URL, payload, timeout=timeout)
                if data.get('done_reason') == 'length':
                    raise ValueError('视觉输出达到长度上限，拒绝使用截断分析')
                text = data.get("message", {}).get("content", "")
            out = extract_json(text)
            CALL_LOG.append({"provider": prov, "vision": True, "seconds": round(time.time() - started, 2),
                             "ok": True})
            out["_provider"] = prov
            return out
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{prov}: {type(exc).__name__} {exc}"[:160])
            CALL_LOG.append({"provider": prov, "vision": True, "seconds": round(time.time() - started, 2),
                             "ok": False, "error": str(exc)[:160]})
    raise RuntimeError("视觉模型都不可用 -> " + " | ".join(errors))
