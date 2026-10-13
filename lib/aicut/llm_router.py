"""v4.0.0 P0：多接口 AI 大脑路由层。

职责：
  · 后端注册表：从 config/aicut.json 的 ai_backends 读取 OpenAI 兼容 / Ollama 原生后端
  · 任务路由：vision / semantic / decision / text 四类任务各自的"降级链"
    （付费 API 快 → 更多付费 API → 本地 Ollama 兜底）
  · 统一调用：llm_chat() 自动按链降级、统一图片编码、统一 token/成本记账
  · 用量日志：每次调用追加一行 JSON 到 work/ai_usage.jsonl
  · 规则兜底：全部 AI 不可用时，decision/text 任务的最小可用输出

设计原则：
  · 只用标准库 + urllib，不引入新依赖（保持 EXE 打包干净）
  · api_key 绝不硬编码，只从 ${ENV_VAR} 占位符解析
  · 付费 API 只发送抽帧图片与文本，不发送原始视频
  · 未配置 ai_backends 时模块处于"未启用"状态，调用方（llm.py）走原逻辑
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from .ollama_endpoint import ollama_base_url

# 四种任务类型
TASK_VISION = "vision"      # 镜头三槽位 / 烧帧图（需多模态）
TASK_SEMANTIC = "semantic"  # 视频级语义摘要
TASK_DECISION = "decision"  # 剪辑决策 JSON
TASK_TEXT = "text"          # 普通文本
TASK_TYPES = (TASK_VISION, TASK_SEMANTIC, TASK_DECISION, TASK_TEXT)

# 本地兜底 Ollama 的默认值（与 v3.9 llm.py 保持一致）
_FALLBACK_OLLAMA_BASE = "http://127.0.0.1:11434"
_FALLBACK_OLLAMA_MODEL = "gemma4:12b"

# Ollama 上下文/生成长度约束（与 llm.py 一致，省显存防啰嗦）
_OLLAMA_NUM_CTX = 8192
_OLLAMA_NUM_PREDICT = 640

_ENV_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


# ---------------------------------------------------------------- 配置读取
_CONFIG_OVERRIDE: str | None = None   # 测试用：临时指向别的配置文件
_USAGE_OVERRIDE: str | None = None    # 测试用：临时指向别的用量日志
_backends_cache: list["Backend"] | None = None


def _config() -> dict:
    """读 aicut.json。生产用 config/aicut.json；测试可通过 set_config_path 覆盖。"""
    if _CONFIG_OVERRIDE:
        p = Path(_CONFIG_OVERRIDE)
        return json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
    try:
        from .paths import CONFIG_DIR
        p = CONFIG_DIR / "aicut.json"
        return json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def set_config_path(path: str | Path | None) -> None:
    """测试用：把路由层的配置文件指向别处（不动真实 aicut.json）。传 None 恢复默认。"""
    global _CONFIG_OVERRIDE, _backends_cache
    _CONFIG_OVERRIDE = str(path) if path else None
    _backends_cache = None


def set_usage_log(path: str | Path | None) -> None:
    """测试用：把用量日志指向别的文件。传 None 恢复 work/ai_usage.jsonl。"""
    global _USAGE_OVERRIDE
    _USAGE_OVERRIDE = str(path) if path else None


def _usage_log_path() -> Path:
    if _USAGE_OVERRIDE:
        return Path(_USAGE_OVERRIDE)
    try:
        from .paths import WORK
        return WORK / "ai_usage.jsonl"
    except Exception:  # noqa: BLE001
        return Path("ai_usage.jsonl")


def router_enabled() -> bool:
    """配置里存在 ai_backends 且非空才算启用；否则调用方走 v3.9 原逻辑。"""
    return bool((_config().get("ai_backends") or []))


def _resolve_env(value: str) -> str:
    """解析 ${ENV_VAR} 占位符；解析失败返回空串（该后端标记不可用，不崩溃）。"""
    if not isinstance(value, str):
        return ""
    m = _ENV_RE.match(value.strip())
    if not m:
        return value  # 不是占位符：按字面量用（本地后端常见）
    name = m.group(1)
    key = os.environ.get(name, "")
    if key:
        return key
    # 与 llm.api_key 一致的回退：PowerShell 用户级环境变量（当前进程可能没继承）
    try:
        from . import llm as _llm  # 延迟导入，避免模块级循环
        return _llm.api_key(name)
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- 后端定义
@dataclass
class Backend:
    name: str
    type: str                     # "ollama" 或 "openai"
    base_url: str
    model: str
    vision_model: str = ""
    enabled: bool = True
    prefer: tuple = ()
    timeout: int = 300
    api_key: str = ""             # 已解析后的真实 key（openai 用）
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    available: bool = True         # key 解析失败 / enabled=False 时 False

    @property
    def supports_vision(self) -> bool:
        """ollama 原生协议自带 images 字段；openai 需配 vision_model 才算支持视觉。"""
        return self.type == "ollama" or bool(self.vision_model)

    @property
    def is_paid(self) -> bool:
        """付费 API = OpenAI 兼容协议且拿到了有效 key（路由时排在本地前面）。"""
        return self.type == "openai" and bool(self.api_key)


def _build_backend(raw: dict) -> Backend:
    name = str(raw.get("name") or raw.get("type") or "unnamed")
    btype = str(raw.get("type") or "ollama").lower()
    api_key = _resolve_env(str(raw.get("api_key") or ""))
    # openai 后端没有可用 key 时标记不可用（不崩溃）
    available = bool(raw.get("enabled", True))
    if btype == "openai" and not api_key:
        available = False
    base_url = raw.get('base_url') or (ollama_base_url() if btype == 'ollama' else '')
    if btype == 'ollama' and os.environ.get('AICUT_OLLAMA_BASE_URL', '').strip():
        base_url = ollama_base_url()
    return Backend(
        name=name, type=btype,
        base_url=str(base_url).rstrip('/'),
        model=str(raw.get("model") or _FALLBACK_OLLAMA_MODEL),
        vision_model=str(raw.get("vision_model") or ""),
        enabled=bool(raw.get("enabled", True)),
        prefer=tuple(raw.get("prefer") or TASK_TYPES),
        timeout=int(raw.get("timeout") or 300),
        api_key=api_key,
        cost_per_1k_input=float(raw.get("cost_per_1k_input") or 0),
        cost_per_1k_output=float(raw.get("cost_per_1k_output") or 0),
        available=available,
    )


def _default_ollama_backend() -> Backend:
    """始终保留的本地兜底后端（config 里没配 ollama 时自动补上）。"""
    vision_model = ""
    try:
        from . import llm as _llm
        vision_model = _llm.get_vision_model()
    except Exception:  # noqa: BLE001
        vision_model = "gemma4:12b"
    return Backend(
        name="ollama-local", type="ollama",
        base_url=ollama_base_url(), model=_FALLBACK_OLLAMA_MODEL,
        vision_model=vision_model, enabled=True, prefer=TASK_TYPES,
        timeout=600, api_key="", available=True,
    )


def load_backends(force: bool = False) -> list[Backend]:
    """加载并缓存后端注册表。"""
    global _backends_cache
    if _backends_cache is not None and not force:
        return _backends_cache
    cfg = _config()
    raws = cfg.get("ai_backends") or []
    backends = [_build_backend(r) for r in raws if isinstance(r, dict)]
    # 兜底：只要启用了路由，就必须保证至少有一个本地 Ollama 后端压底
    if not any(b.type == "ollama" and b.available for b in backends):
        backends.append(_default_ollama_backend())
    _backends_cache = backends
    return backends


def route_backends(task_type: str) -> list[Backend]:
    """返回某任务类型的降级链：付费 API（按配置顺序）→ 本地 Ollama。

    vision 任务只选支持视觉的后端；global_paid_api_enabled=false 时剔除全部付费 API。
    """
    cfg = _config()
    router_cfg = cfg.get("ai_router") or {}
    paid_api_on = bool(router_cfg.get("global_paid_api_enabled", True))
    chain: list[Backend] = []
    for b in load_backends():
        if not b.enabled or not b.available:
            continue
        if task_type not in b.prefer:
            continue
        if task_type == TASK_VISION and not b.supports_vision:
            continue
        if not paid_api_on and b.is_paid:
            continue  # 隐私模式：完全不走付费 API
        chain.append(b)
    # 排序：付费 API 在前（快），本地在后，同类内保持配置顺序
    paid = [b for b in chain if b.is_paid]
    local = [b for b in chain if not b.is_paid]
    unique, seen = [], set()
    for backend in paid + local:
        identity = (backend.type, backend.base_url.rstrip('/'), backend.model, backend.vision_model)
        if identity not in seen:
            seen.add(identity)
            unique.append(backend)
    return unique


# route() 是规格里的旧名，保留别名
route = route_backends


# ---------------------------------------------------------------- HTTP 层（可被测试 monkeypatch）
def _post_json(url: str, payload: dict, headers: dict | None = None,
               timeout: int = 300) -> dict:
    """urllib POST JSON，返回解析后的 dict。独立成函数便于测试 mock。"""
    import urllib.request
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


def _encode_images(images: list[str | Path]) -> list[str]:
    return [base64.b64encode(Path(p).read_bytes()).decode("ascii") for p in images]


def _estimate_tokens(text: str) -> int:
    """本地模型没有 usage 字段：中文 1 字 ≈ 1.5 token，英文约 4 字符 ≈ 1 token。"""
    if not text:
        return 0
    zh = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - zh
    return int(zh * 1.5 + other / 4) + 1


# ---------------------------------------------------------------- 单后端调用
def _call_glm_local(b: Backend, messages: list[dict], *, images: list,
                    json_mode: bool, temperature: float, timeout: int) -> tuple[str, int, int]:
    """llama-cli 直调 GLM-4.6V-Flash（type="glm-local"）。

    vision 任务：取第一张图片路径走 analyze_frame（深度理解，8GB 实测约 3-4 分钟/帧）；
    文本任务：走 analyze_text（CLI 文本模式）。
    """
    from . import bundled_vision
    if not bundled_vision.is_available():
        raise RuntimeError(bundled_vision.health().get("error", "内置视觉模型不可用"))
    prompt = ""
    for m in reversed(messages):
        c = m.get("content", "")
        if isinstance(c, str) and c.strip():
            prompt = c
            break
        if isinstance(c, list):
            texts = [p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"]
            prompt = " ".join(texts).strip()
            if prompt:
                break
    if images:
        out = bundled_vision.analyze_frame(
            images[0], prompt=prompt or "请分析画面并输出 JSON。",
            temperature=temperature, timeout=timeout)
    else:
        from . import glm_deep
        out = glm_deep.analyze_text(prompt or "你好", temperature=temperature, timeout=timeout)
    if "error" in out and "raw" not in out:
        raise RuntimeError(f"GLM 调用失败: {out.get('error')}")
    text = json.dumps(out, ensure_ascii=False) if json_mode else str(out)
    return text, 0, 0


def _call_ollama(b: Backend, messages: list[dict], *, images_b64: list[str],
                 json_mode: bool, temperature: float, timeout: int,
                 max_output_tokens: int | None = None) -> tuple[str, int, int]:
    """走 Ollama 原生 /api/chat。images 挂在最后一条 user 消息的 images 字段。

    v4.0.0 提速修复：视觉请求（带图）优先用 b.vision_model（如 qwen2.5vl:7b），
    避免本地视觉仍落到 26B gemma4（CPU 单次推理可达 300s+）。
    """
    model = (b.vision_model or b.model) if images_b64 else b.model
    msgs = [dict(m) for m in messages]
    if images_b64:
        for m in reversed(msgs):  # 挂到最后一条 user 消息上
            if m.get("role") == "user":
                m["images"] = images_b64
                break
    output_limit = max_output_tokens or _OLLAMA_NUM_PREDICT
    # Images are vision inputs, not thousands of base64 text tokens.
    text_msgs = [{key: value for key, value in message.items() if key != 'images'} for message in msgs]
    needed = (_estimate_tokens(json.dumps(text_msgs, ensure_ascii=False))
              + 4096 * len(images_b64) + output_limit + 512)
    context_limit = next((size for size in (8192, 16384, 32768) if size >= needed), None)
    if context_limit is None:
        raise ValueError('输入知识与输出预算超过 32768 上下文，请分段处理或缩小检索预算')
    payload = {
        "model": model,
        "messages": msgs,
        "stream": False,
        "keep_alive": "30m",
        "think": False,  # 与 llm.py 一致：关闭思考模式防止吃光 num_predict
        "options": {"temperature": temperature,
                    "num_ctx": context_limit, "num_predict": output_limit},
    }
    if json_mode:
        payload["format"] = "json"
    try:
        data = _post_json(f"{b.base_url}/api/chat", payload, timeout=timeout)
    except Exception as exc:
        from urllib.error import HTTPError
        # Only recover a missing model, not transport failures or visual incompatibility.
        if not isinstance(exc, HTTPError) or exc.code != 404 or images_b64:
            raise
        from urllib.request import urlopen
        with urlopen(f"{b.base_url}/api/tags", timeout=min(timeout, 10)) as response:
            installed = {row['name'] for row in json.load(response).get('models', [])}
        if model in installed:
            raise
        preferred = [os.environ.get('LOCAL_MODEL', ''), 'gemma4-26b-lite-32k',
                     'gemma4:26b', 'gemma4:e2b', 'qwen3.8:27b', 'qwen3.6:35b']
        replacement = next((name for name in preferred if name and name in installed), None)
        if not replacement:
            raise
        payload['model'] = replacement
        data = _post_json(f"{b.base_url}/api/chat", payload, timeout=timeout)
        # Keep the backend identity truthful for subsequent calls and usage logs.
        b.model = replacement
    if data.get('done_reason') == 'length':
        raise ValueError('模型输出达到长度上限，拒绝将截断结果作为完整决策')
    text = data.get("message", {}).get("content", "") or ""
    # ollama 响应无 usage，按字符数估算
    in_tok = _estimate_tokens(json.dumps(messages, ensure_ascii=False))
    out_tok = _estimate_tokens(text)
    return text, in_tok, out_tok


def _call_openai(b: Backend, messages: list[dict], *, images_b64: list[str],
                 vision: bool, json_mode: bool, temperature: float,
                 timeout: int, max_output_tokens: int | None = None) -> tuple[str, int, int]:
    """走 OpenAI 兼容 /chat/completions。图片用 image_url data URL 内联。"""
    model = b.vision_model if (vision and b.vision_model) else b.model
    msgs = [dict(m) for m in messages]
    if images_b64:
        for m in reversed(msgs):
            if m.get("role") == "user":
                content = m.get("content", "")
                parts = [{"type": "text", "text": str(content)}]
                for b64 in images_b64:
                    parts.append({"type": "image_url",
                                  "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                m["content"] = parts
                break
    payload = {"model": model, "messages": msgs, "temperature": temperature, "stream": False}
    if max_output_tokens is not None:
        payload['max_tokens'] = max_output_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _post_json(
        f"{b.base_url}/chat/completions", payload,
        {"Authorization": f"Bearer {b.api_key}"}, timeout=timeout)
    text = data["choices"][0]["message"].get("content", "") or ""
    usage = data.get("usage") or {}
    in_tok = int(usage.get("prompt_tokens") or 0) or _estimate_tokens(json.dumps(messages, ensure_ascii=False))
    out_tok = int(usage.get("completion_tokens") or 0) or _estimate_tokens(text)
    return text, in_tok, out_tok


# ---------------------------------------------------------------- 用量记账
LAST_USED_BACKEND: str = ""   # 本次 llm_chat 最终命中的后端名（llm.py 用它填 _provider）


def _log_usage(rec: dict) -> None:
    """追加一行 JSON 到 work/ai_usage.jsonl；写日志失败不影响主流程。"""
    if not (_config().get("ai_router") or {}).get("log_usage", True):
        return
    try:
        path = _usage_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def usage_summary() -> dict:
    """累计统计：总调用数 / 成功率 / 各后端耗时 / 总成本估算。"""
    total = ok = 0
    by_backend: dict[str, dict] = {}
    total_cost = 0.0
    try:
        with open(_usage_log_path(), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                total += 1
                if rec.get("ok"):
                    ok += 1
                name = rec.get("backend", "?")
                slot = by_backend.setdefault(name, {"calls": 0, "ok": 0, "seconds": 0.0, "cost": 0.0})
                slot["calls"] += 1
                slot["ok"] += int(bool(rec.get("ok")))
                slot["seconds"] += float(rec.get("seconds") or 0)
                slot["cost"] += float(rec.get("cost_estimate") or 0)
                total_cost += float(rec.get("cost_estimate") or 0)
    except FileNotFoundError:
        pass
    return {"total_calls": total, "success": ok,
            "success_rate": round(ok / total, 4) if total else 0.0,
            "total_cost_estimate": round(total_cost, 6), "by_backend": by_backend}


# ---------------------------------------------------------------- 规则兜底
def _rule_fallback(task_type: str) -> str:
    """全部 AI 后端不可用时的确定性最小输出。"""
    if task_type == TASK_DECISION:
        # 默认剪辑参数：空片段列表 + 说明，调用方应能安全跳过
        return json.dumps({"clips": [], "note": "AI 全部不可用，规则兜底输出"}, ensure_ascii=False)
    if task_type == TASK_TEXT:
        print("[llm_router][警告] 全部 AI 后端不可用，text 任务返回空串")
        return ""
    # vision / semantic 必须看图，规则给不出可信结果，抛明确异常
    raise RuntimeError(f"全部 AI 后端不可用，且 {task_type} 任务不支持规则兜底")


# ---------------------------------------------------------------- 统一调用入口
def llm_chat(messages: list[dict], task_type: str = TASK_TEXT,
             images: list[str | Path] | None = None,
             json_mode: bool = False, temperature: float = 0.2,
             timeout: int | None = None, max_retries: int = 1,
             allow_fallback_rules: bool = False,
             max_output_tokens: int | None = None) -> str:
    """按降级链依次尝试所有可用后端，返回纯文本结果。

    失败原因（超时 / 429 / 401 / 网络异常 / JSON 解析失败）自动降级到下一个后端；
    全部失败时：allow_fallback_rules=True 走规则兜底，否则抛 RuntimeError。
    """
    global LAST_USED_BACKEND
    chain = route_backends(task_type)
    if not chain:
        if allow_fallback_rules:
            return _rule_fallback(task_type)
        raise RuntimeError(f"没有可用后端支撑任务类型：{task_type}")

    images_b64 = _encode_images(images or [])
    attempts = max(1, int(max_retries))
    errors: list[str] = []

    for b in chain:
        per_timeout = timeout or b.timeout or 300
        for attempt in range(attempts):
            started = time.time()
            rec = {"ts": datetime.now().isoformat(timespec="seconds"),
                   "task_type": task_type, "backend": b.name, "ok": False,
                   "seconds": 0.0, "input_tokens": 0, "output_tokens": 0,
                   "cost_estimate": 0.0, "error": None}
            try:
                if b.type == "ollama":
                    text, in_tok, out_tok = _call_ollama(
                        b, messages, images_b64=images_b64,
                        json_mode=json_mode, temperature=temperature, timeout=per_timeout,
                        max_output_tokens=max_output_tokens)
                elif b.type == "openai":
                    text, in_tok, out_tok = _call_openai(
                        b, messages, images_b64=images_b64,
                        vision=bool(images_b64) or task_type == TASK_VISION,
                        json_mode=json_mode, temperature=temperature, timeout=per_timeout,
                        max_output_tokens=max_output_tokens)
                elif b.type == "glm-local":
                    text, in_tok, out_tok = _call_glm_local(
                        b, messages, images=images or [], json_mode=json_mode,
                        temperature=temperature, timeout=per_timeout)
                else:
                    raise ValueError(f"未知后端类型：{b.type}")
                if not text.strip():
                    raise RuntimeError("模型返回空内容")
                # json_mode 时校验可解析，解析失败算该后端失败，自动降级
                if json_mode:
                    _ = json.loads(_strip_fence(text))
                cost = (in_tok / 1000) * b.cost_per_1k_input + \
                       (out_tok / 1000) * b.cost_per_1k_output
                rec.update(ok=True, seconds=round(time.time() - started, 3),
                           input_tokens=in_tok, output_tokens=out_tok,
                           cost_estimate=round(cost, 6))
                _log_usage(rec)
                LAST_USED_BACKEND = b.name
                return text
            except Exception as exc:  # noqa: BLE001 —— 任何失败都降级到下一后端
                reason = f"{type(exc).__name__}: {exc}"
                rec["error"] = reason[:200]
                rec["seconds"] = round(time.time() - started, 3)
                _log_usage(rec)
                errors.append(f"{b.name}(第{attempt + 1}次): {reason}"[:200])
                continue  # 同一后端重试
            break  # 成功就不会走到这；失败则换后端
    # 全部后端全挂
    if allow_fallback_rules:
        out = _rule_fallback(task_type)
        print(f"[llm_router][警告] 全部后端失败，已走规则兜底：{' | '.join(errors)}")
        return out
    raise RuntimeError("AI 路由全部后端不可用 -> " + " | ".join(errors))


def _strip_fence(text: str) -> str:
    """去掉 ```json 围栏（json_mode 校验用，与 llm.extract_json 同思路）。"""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"```\s*$", "", s)
    return s.strip()
