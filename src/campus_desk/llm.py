"""统一 LLM 构造（M5-T3）：4 处 _default_llm 收敛到 build_llm()。

参数与旧实现完全一致（4 处现有调用点为依据）：
- model/api_key/base_url/temperature=0/timeout=30
- model_kwargs 构造期带 response_format={"type": "json_object"}（DeepSeek
  结构化输出铁律：json_object 模式必须在构造期声明，见 intent.py 注释；
  调用点从不传 response_format，此行为保持）

埋点：enabled() 时挂 langfuse CallbackHandler（LLM call 全量进 trace）；
无 key 时 langfuse_handler() 返回 None → 不传 callbacks（llm.callbacks=None）。
"""

from langchain_openai import ChatOpenAI

from campus_desk import telemetry
from campus_desk.config import settings


def build_llm() -> ChatOpenAI:
    """构造 DeepSeek ChatOpenAI 实例（enabled 时挂 langfuse handler）。"""
    kwargs = {
        "model": settings.deepseek_model,
        "api_key": settings.deepseek_api_key,
        "base_url": "https://api.deepseek.com",
        "temperature": 0,
        "timeout": 30,
        "model_kwargs": {"response_format": {"type": "json_object"}},
    }
    handler = telemetry.langfuse_handler()
    if handler is not None:
        kwargs["callbacks"] = [handler]
    return ChatOpenAI(**kwargs)
