"""Langfuse 埋点封装（M5-T3）：无 key 全禁用，惰性 import 从根上规避未初始化错误。

设计铁律：
1. enabled() == False（LANGFUSE 双 key 未配齐，本机常态）时所有函数零副作用
   no-op——langfuse 包不会被 import（tests/test_telemetry.py 用 sys.modules
   断言锁死该设计），不发任何网络请求、不抛任何异常。
2. enabled() == True 时才惰性 import langfuse 并创建 client / CallbackHandler
   （模块级缓存，进程内单例）。
3. v3 API（langfuse 4.14.2 实测签名）：
   - start_as_current_observation(*, name, as_type="span", metadata=...)：
     context manager，自动关闭 span
   - propagate_attributes(*, user_id, session_id, tags, trace_name, ...)：
     context manager，为当前 trace 设置属性
   - get_client().flush()：短生命周期脚本冲刷上报
"""

from collections.abc import Iterator
from contextlib import contextmanager

from campus_desk.config import settings

# 模块级缓存（enabled 后才被赋值；无 key 全程保持 None）
_client = None
_handler = None


def enabled() -> bool:
    """埋点开关：LANGFUSE 公钥私钥都配了才启用（见 config.langfuse_enabled）。"""
    return settings.langfuse_enabled


def _ensure_client():
    """惰性初始化 Langfuse 客户端（仅 enabled() 为 True 时调用）。"""
    global _client
    if _client is None:
        from langfuse import Langfuse  # 惰性 import：无 key 时包不被加载

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_host,
        )
    return _client


def langfuse_handler():
    """LangChain CallbackHandler 惰性单例（LLM call 埋点用）。

    无 key 返回 None——调用方据此决定是否挂 callbacks（build_llm 使用）。
    """
    global _handler
    if not enabled():
        return None
    if _handler is None:
        from langfuse.langchain import CallbackHandler  # 惰性 import

        _handler = CallbackHandler()
    return _handler


@contextmanager
def span(name: str, *, metadata: dict | None = None) -> Iterator[None]:
    """包一个 span（agent 步骤 / 工具调用 / 状态跳转）。无 key 时纯 no-op。

    参数求值开销仅一个 dict 字面量，enabled 为 False 时无任何网络/导入行为。
    """
    if not enabled():
        yield None
        return
    kwargs = {"name": name, "as_type": "span"}
    if metadata:
        kwargs["metadata"] = metadata
    with _ensure_client().start_as_current_observation(**kwargs):
        yield None


@contextmanager
def trace_attrs(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    trace_name: str | None = None,
) -> Iterator[None]:
    """为当前 trace 设置 user/session/tags 属性。无 key 时纯 no-op。"""
    if not enabled():
        yield None
        return
    from langfuse import propagate_attributes  # 惰性 import

    kwargs = {}
    if user_id is not None:
        kwargs["user_id"] = user_id
    if session_id is not None:
        kwargs["session_id"] = session_id
    if tags is not None:
        kwargs["tags"] = tags
    if trace_name is not None:
        kwargs["trace_name"] = trace_name
    with propagate_attributes(**kwargs):
        yield None


def flush() -> None:
    """冲刷未上报的 span 事件（短生命周期脚本/评测结束调用）。无 key 时 no-op。"""
    if not enabled():
        return
    from langfuse import get_client  # 惰性 import

    get_client().flush()
