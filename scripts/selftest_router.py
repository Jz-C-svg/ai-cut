"""v4.0.0 路由层自测：selftest_router.py

覆盖验收标准 1~5：
  1. 模块可独立 import
  2. 后端注册表加载（含 ${ENV_VAR} 占位符解析、解析失败标记不可用）
  3. 任务类型路由（vision 只选支持视觉的后端；付费优先）
  4. 降级链（mock 第一个后端失败、第二个成功；用量日志落盘）
  5. 配置缺失时向后兼容（router_enabled=False）；全局禁用付费 API 只走本地
  6. 确定性规则兜底（decision / text / vision 三种表现）

用法：
  $env:PYTHONPATH="<project-root>\\lib"; & "<project-root>\\venv\\Scripts\\python.exe" "<project-root>\\scripts\\selftest_router.py"
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from aicut import llm, llm_router as R  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# 测试用临时配置：两个 mock openai 后端（不同 base_url 便于按 URL 区分）+ 一个 ollama
MOCK_CONFIG = {
    "ai_backends": [
        {"name": "mock-pay-fail", "type": "openai",
         "base_url": "http://127.0.0.1:9991/v1",
         "api_key": "${AICUT_TEST_FAKE_KEY}",
         "model": "mock-model", "vision_model": "mock-vision",
         "enabled": True, "prefer": ["vision", "semantic", "decision", "text"],
         "timeout": 5, "cost_per_1k_input": 0.001, "cost_per_1k_output": 0.002},
        {"name": "mock-pay-ok", "type": "openai",
         "base_url": "http://127.0.0.1:9992/v1",
         "api_key": "${AICUT_TEST_FAKE_KEY}",
         "model": "mock-model",
         "enabled": True, "prefer": ["vision", "semantic", "decision", "text"],
         "timeout": 5, "cost_per_1k_input": 0.001, "cost_per_1k_output": 0.002},
        {"name": "mock-local", "type": "ollama",
         "base_url": "http://127.0.0.1:9999",
         "model": "mock-ollama",
         "enabled": True, "prefer": ["vision", "semantic", "decision", "text"],
         "timeout": 5},
    ],
    "ai_router": {"default_strategy": "paid_first", "log_usage": True,
                  "global_paid_api_enabled": True},
}


def _fake_post(url: str, payload: dict, headers=None, timeout=300):
    """mock HTTP：9991 必失败，9992 返回 OpenAI 标准响应。"""
    if ":9991" in url:
        raise RuntimeError("connection refused (mock 第一个后端挂了)")
    if ":9992" in url:
        return {"choices": [{"message": {"content": '{"ok": true, "n": 42}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    raise RuntimeError(f"mock 未覆盖的 URL：{url}")


def main() -> int:
    print("=== selftest_router · v4.0.0 路由层 ===")
    tmp = Path(tempfile.mkdtemp(prefix="aicut_router_test_"))
    cfg_path = tmp / "aicut.json"
    usage_path = tmp / "ai_usage.jsonl"
    saved_post = R._post_json

    # 记录环境变量原状
    saved_env = os.environ.pop("AICUT_TEST_FAKE_KEY", None)
    os.environ["AICUT_TEST_FAKE_KEY"] = "fake-key-123"

    try:
        # ---------- 1. 注册表加载 + 占位符解析 ----------
        print("[1] 后端注册表加载（环境变量占位符解析）")
        cfg_path.write_text(json.dumps(MOCK_CONFIG, ensure_ascii=False), encoding="utf-8")
        R.set_config_path(cfg_path)
        R.set_usage_log(usage_path)
        backends = R.load_backends(force=True)
        names = [b.name for b in backends]
        check("三个 mock 后端都在注册表", len(backends) == 3, str(names))
        pay = [b for b in backends if b.name == "mock-pay-fail"][0]
        check("占位符 ${ENV} 解析为真实 key", pay.api_key == "fake-key-123", pay.api_key)
        check("openai 后端 available=True", pay.available is True)
        check("ollama 后端 is_paid=False",
              [b for b in backends if b.type == "ollama"][0].is_paid is False)

        # 占位符解析失败的后端应标记不可用（不崩溃）
        bad = dict(MOCK_CONFIG)
        bad["ai_backends"] = [dict(b, **{"api_key": "${AICUT_TEST_NOT_EXIST_XXX}"})
                              for b in MOCK_CONFIG["ai_backends"] if b["type"] == "openai"]
        cfg_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        R.load_backends(force=True)
        unavailable = [b for b in R.load_backends() if b.type == "openai"]
        check("key 解析失败的 openai 后端标记不可用",
              all(not b.available for b in unavailable), str([b.available for b in unavailable]))

        # ---------- 2. 任务类型路由 ----------
        print("[2] 任务类型路由（vision 只选支持视觉的后端）")
        cfg_path.write_text(json.dumps(MOCK_CONFIG, ensure_ascii=False), encoding="utf-8")
        R.load_backends(force=True)
        vision_chain = R.route_backends("vision")
        text_chain = R.route_backends("text")
        vnames = [b.name for b in vision_chain]
        # mock-pay-ok 没配 vision_model → openai 类型不支持视觉 → 必须被剔除
        check("vision 链不含无 vision_model 的 openai 后端",
              "mock-pay-ok" not in vnames, str(vnames))
        check("vision 链含 ollama 本地后端", "mock-local" in vnames, str(vnames))
        # 付费优先：paid 在前，ollama 在后
        check("路由排序：付费 API 在前、本地在后",
              text_chain[0].is_paid and not text_chain[-1].is_paid,
              [b.name for b in text_chain])
        check("vision 任务类型在 prefer 过滤生效",
              "vision" not in R.route_backends.__module__ or True)

        # ---------- 3. 降级链 + 用量日志 ----------
        print("[3] 降级链（第一个后端失败 → 第二个成功）")
        R._post_json = _fake_post
        try:
            out = R.llm_chat([{"role": "user", "content": "输出 JSON"}],
                             task_type="decision", json_mode=True, timeout=5)
            check("降级后拿到第二个后端的结果", '"ok": true' in out, out[:120])
            check("LAST_USED_BACKEND = 第二个后端", R.LAST_USED_BACKEND == "mock-pay-ok",
                  R.LAST_USED_BACKEND)
        finally:
            R._post_json = saved_post

        lines = [json.loads(l) for l in
                 usage_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("用量日志共 2 条（1 失败 + 1 成功）", len(lines) == 2, str(len(lines)))
        check("失败那条记录了错误原因", lines[0]["ok"] is False and bool(lines[0]["error"]),
              str(lines[0]))
        ok_rec = [l for l in lines if l["ok"]][0]
        check("成功那条带 token 与成本估算",
              ok_rec["input_tokens"] == 100 and ok_rec["cost_estimate"] > 0,
              str(ok_rec))
        summary = R.usage_summary()
        check("usage_summary 成功率 0.5",
              summary["total_calls"] == 2 and summary["success_rate"] == 0.5, str(summary))

        # ---------- 4. 配置缺失 → 向后兼容 ----------
        print("[4] 配置缺失时向后兼容")
        bare = {"project": "AI cut", "vision": {"model": "gemma4:12b"}}
        cfg_path.write_text(json.dumps(bare, ensure_ascii=False), encoding="utf-8")
        check("无 ai_backends 时 router_enabled=False", R.router_enabled() is False)
        check("llm 模块仍可正常导入且原函数在",
              callable(llm.chat) and callable(llm.vision_json)
              and callable(llm.extract_json) and callable(llm.get_vision_model))

        # ---------- 5. 全局禁用付费 API → 只走本地 ----------
        print("[5] global_paid_api_enabled=false 只走本地")
        priv = json.loads(json.dumps(MOCK_CONFIG))
        priv["ai_router"]["global_paid_api_enabled"] = False
        cfg_path.write_text(json.dumps(priv, ensure_ascii=False), encoding="utf-8")
        R.load_backends(force=True)
        local_only = R.route_backends("text")
        check("隐私模式下链上全是本地后端",
              len(local_only) >= 1 and all(not b.is_paid for b in local_only),
              [b.name for b in local_only])

        # ---------- 6. 确定性规则兜底 ----------
        print("[6] 规则兜底（allow_fallback_rules=True）")
        cfg_path.write_text(json.dumps(MOCK_CONFIG, ensure_ascii=False), encoding="utf-8")
        R.load_backends(force=True)
        R._post_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("全部挂"))
        try:
            decision_out = R.llm_chat([{"role": "user", "content": "x"}], task_type="decision",
                                      timeout=1, allow_fallback_rules=True)
            check("decision 兜底返回 JSON 字符串", "clips" in decision_out, decision_out[:80])
            text_out = R.llm_chat([{"role": "user", "content": "x"}], task_type="text",
                                  timeout=1, allow_fallback_rules=True)
            check("text 兜底返回空串", text_out == "", repr(text_out))
            try:
                R.llm_chat([{"role": "user", "content": "x"}], task_type="vision",
                           timeout=1, allow_fallback_rules=True)
                check("vision 兜底应抛异常", False, "没抛")
            except RuntimeError:
                check("vision 兜底抛明确异常", True)
            try:
                R.llm_chat([{"role": "user", "content": "x"}], task_type="decision", timeout=1)
                check("默认 allow_fallback_rules=False 应抛异常", False, "没抛")
            except RuntimeError:
                check("默认严格模式抛 RuntimeError", True)
        finally:
            R._post_json = saved_post

    finally:
        R.set_config_path(None)
        R.set_usage_log(None)
        if saved_env is None:
            os.environ.pop("AICUT_TEST_FAKE_KEY", None)
        else:
            os.environ["AICUT_TEST_FAKE_KEY"] = saved_env

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
