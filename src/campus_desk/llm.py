"""统一 LLM 构造（M5-T3 收敛 4 处；M2-T4 拆 _base_kwargs + 新增 build_tool_llm）。

- build_llm：结构化输出场景——model_kwargs 构造期带 response_format={"type":
  "json_object"}（DeepSeek 铁律：构造期声明 + prompt 必须含 "json" 字样，见
  intent.py 注释；调用点从不传 response_format，此行为保持）
- build_tool_llm（M2）：工具调用场景——不带 response_format（json_object 会抑制
  tool_calls）；2026-08-16 实测裸 bind_tools 全链路可用。FC 的 strict 工具 schema
  见 query/tools.py

埋点（两个独立 handler，同一次 invoke 各自触发，无需去重）：
- langfuse：enabled() 时挂 CallbackHandler（观测）；无 key 时 langfuse_handler()
  返回 None → 不传
- usage（M13）：**无条件**挂载 UsageCallbackHandler（本地成本记账）。不依赖任何
  key；无 DATABASE_URL 时落库在 usage._write_usage 内被吞，不影响调用
"""

from langchain_openai import ChatOpenAI

from campus_desk import telemetry, usage
from campus_desk.config import settings


def _base_kwargs() -> dict:
    """公共构造参数：model/base_url/temperature/timeout + 条件 api_key + langfuse handler。"""
    kwargs = {
        "model": settings.deepseek_model,
        "base_url": "https://api.deepseek.com",
        "temperature": 0,
        "timeout": 30,
    }
    # api_key 有值才显式传（openai 2.53 起 api_key="" 构造即抛 Missing credentials）：
    # 无 key 时交 SDK 读 OPENAI_API_KEY 环境变量——CI/无 .env 环境可"构造不调用"
    if settings.deepseek_api_key:
        kwargs["api_key"] = settings.deepseek_api_key
    callbacks = []
    handler = telemetry.langfuse_handler()
    if handler is not None:
        callbacks.append(handler)
    callbacks.append(usage.usage_handler())  # M13：本地计量无条件挂载
    kwargs["callbacks"] = callbacks
    return kwargs


def build_llm() -> ChatOpenAI:
    """构造 DeepSeek 实例（结构化输出：构造期声明 json_object）。"""
    kwargs = _base_kwargs()
    kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
    return ChatOpenAI(**kwargs)


def build_tool_llm() -> ChatOpenAI:
    """构造工具调用实例（M2）：无 response_format，供 bind_tools 真 FC。"""
    return ChatOpenAI(**_base_kwargs())
