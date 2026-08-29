"""LLM 调用计量埋点（M13-ZJUT）：CallbackHandler 拦截 usage 落库 `llm_usage`。

设计铁律（与 audit/telemetry 同风格）：
1. **零侵入业务代码**：handler 在 `llm.py` 构造期挂载到 LLM 实例，业务调用点
   只需在 invoke 时带一个 `config={"tags":["call_point:xxx"]}`；ContextVar 携带
   归属信息（user_id/thread_id/route），由 `orchestrator.turn` 入口设置、finally 清理。
2. **旁路语义**：`_write_usage` 独立会话写入，整体 try/except 吞异常——计量失败
   （无 DATABASE_URL / 表不存在 / 连接抖动）绝不阻断对话。
3. **只记事实不记钱**：落库只有 token 三件套 + 归属 + 调用点 + 模型 + 状态；
   费用一律由 `scripts/cost_report.py` 按当前 config 单价派生（单价可变，改价不重算历史）。
4. **错误路径也记**：`on_llm_error` 记 status="error"、token 记 0——成本视角必须看见失败调用
   （失败重试也是钱），报表可按 status 过滤。

与 Langfuse 的关系：两者是并存的独立 handler（观测 vs 本地记账），同一 invoke 各自
触发，无需去重。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from campus_desk.config import settings
from campus_desk.db.models import LLMUsage
from campus_desk.db.session import SessionFactory

# 调用点常量（3 个运行时 LLM 调用点；未打 tag 的调用记 unknown）
CALL_POINT_INTENT = "intent"
CALL_POINT_DECIDE = "decide"
CALL_POINT_TOOL_SELECT = "tool_select"
CALL_POINT_UNKNOWN = "unknown"

_TAG_PREFIX = "call_point:"

# 归属上下文（orchestrator.turn 设置，handler 读取）。默认 None = 无归属（脚本直调 LLM）
_usage_ctx: ContextVar[dict[str, Any] | None] = ContextVar("campus_desk_usage_ctx", default=None)
# 当前调用点（调用点 context manager 设置；比 invoke 的 config tags 更通用，见 call_point 注释）
_call_point_var: ContextVar[str | None] = ContextVar("campus_desk_call_point", default=None)

# 会话工厂注入点（测试注入 SQLite；None = 用 settings.database_url 的 MySQL 工厂）
_session_factory: SessionFactory | None = None
_factory_cache: SessionFactory | None = None


def configure(session_factory: SessionFactory | None) -> None:
    """注入会话工厂（测试/脚本用）；传 None 恢复默认（按 settings.database_url）。"""
    global _session_factory, _factory_cache
    _session_factory = session_factory
    _factory_cache = None


def set_usage_ctx(
    *,
    user_id: str | None = None,
    thread_id: str | None = None,
    route: str | None = None,
) -> None:
    """设置当前上下文的归属信息（覆盖式）。"""
    _usage_ctx.set({"user_id": user_id, "thread_id": thread_id, "route": route})


def patch_usage_ctx(**kwargs: Any) -> None:
    """局部更新归属信息（如 orchestrator 各分支确定最终 route 后回写）。"""
    ctx = dict(_usage_ctx.get() or {})
    ctx.update(kwargs)
    _usage_ctx.set(ctx)


def get_usage_ctx() -> dict[str, Any]:
    """读取当前归属信息（无则为全 None 字典，便于调用方直接 ** 展开）。"""
    ctx = _usage_ctx.get()
    return dict(ctx) if ctx else {"user_id": None, "thread_id": None, "route": None}


def clear_usage_ctx() -> None:
    """清理归属上下文（orchestrator 每轮 finally 调用，防跨请求串味）。"""
    _usage_ctx.set(None)


@contextmanager
def usage_ctx(
    *, user_id: str | None = None, thread_id: str | None = None, route: str | None = None
):
    """设置/清理归属上下文的 context manager（脚本与评测入口用）。"""
    set_usage_ctx(user_id=user_id, thread_id=thread_id, route=route)
    try:
        yield None
    finally:
        clear_usage_ctx()


def _trim(value: Any, limit: int) -> str:
    """截断到列宽（MySQL 严格模式超长报 1406，SQLite 测不出——落库前统一截）。"""
    return str(value or "")[:limit]


@contextmanager
def call_point(name: str):
    """标记一段代码内的 LLM 调用点（ContextVar，handler 读取）。

    实现偏离说明（M13 计划 §2④ 原定 `invoke(config={"tags":[...]})`）：
    测试与评测注入的 Fake/stub LLM 只实现 `invoke(messages)` 单参签名，传 config
    会 TypeError（被调用点 try/except 吞掉后测试静默失真）。ContextVar 对调用签名
    零要求——真 LLM（handler 同步回调同线程同上下文）与 Fake 都能正确标记。
    tags 仍保留为 handler 的兜底识别路径（RunnableConfig 里打了 tag 也能识别，优先）。
    """
    token = _call_point_var.set(name)
    try:
        yield None
    finally:
        _call_point_var.reset(token)


def _resolve_call_point(tags: list[str] | None = None) -> str:
    """调用点取值优先级：invoke 的 `call_point:` tag → ContextVar → unknown。"""
    for tag in tags or []:
        if isinstance(tag, str) and tag.startswith(_TAG_PREFIX):
            name = tag[len(_TAG_PREFIX) :].strip()
            if name:
                return name
    return _call_point_var.get() or CALL_POINT_UNKNOWN


def _usage_dicts(response: Any) -> list[dict]:
    """把 LLMResult 里可能藏 usage 的位置摊平成候选 dict 列表（按优先级）。"""
    out: list[dict] = []
    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        out.append(llm_output)
        for key in ("token_usage", "usage"):
            nested = llm_output.get(key)
            if isinstance(nested, dict):
                out.append(nested)
    for gens in getattr(response, "generations", None) or []:
        for gen in gens or []:
            info = getattr(gen, "generation_info", None)
            if isinstance(info, dict):
                out.append(info)
                for key in ("token_usage", "usage"):
                    nested = info.get(key)
                    if isinstance(nested, dict):
                        out.append(nested)
    return out


def _extract_usage(response: Any) -> dict[str, int]:
    """抽取 token 三件套（纯函数，便于单测）。

    优先 `llm_output.token_usage`，其次 `generations[][].generation_info.token_usage`
    （不同 provider/model 落点不同）；都没有 → 全 0（不抛）。
    total 缺失时按 prompt+completion 推导。
    """
    for cand in _usage_dicts(response):
        if "prompt_tokens" in cand or "total_tokens" in cand:
            prompt = int(cand.get("prompt_tokens") or 0)
            completion = int(cand.get("completion_tokens") or 0)
            total = int(cand.get("total_tokens") or (prompt + completion))
            return {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
            }
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _extract_model(response: Any, kwargs: dict | None = None) -> str:
    """抽取模型名：invocation_params → llm_output → generation_info → settings 兜底。"""
    params = (kwargs or {}).get("invocation_params")
    if isinstance(params, dict):
        for key in ("model", "model_name", "_model"):
            if params.get(key):
                return str(params[key])
    for cand in _usage_dicts(response):
        for key in ("model_name", "model"):
            if cand.get(key):
                return str(cand[key])
    return settings.deepseek_model


def _write_usage(
    *,
    user_id: str | None = None,
    thread_id: str | None = None,
    route: str | None = None,
    call_point: str = CALL_POINT_UNKNOWN,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    status: str = "success",
) -> bool:
    """写一行计量（独立事务 + 异常吞掉）。返回是否落库成功（测试/排障用）。

    无 DATABASE_URL 时 `default_session_factory()` 抛 RuntimeError → 直接返回 False，
    不影响主流程（测试构造 LLM 但不开库的场景）。
    """
    global _factory_cache
    try:
        factory = _session_factory
        if factory is None:
            if _factory_cache is None:
                from campus_desk.db.session import default_session_factory

                _factory_cache = default_session_factory()
            factory = _factory_cache
    except Exception:  # noqa: BLE001 — 计量旁路：无数据源就静默放弃
        return False

    try:
        with factory() as session, session.begin():
            session.add(
                LLMUsage(
                    user_id=_trim(user_id, 32) or None,
                    thread_id=_trim(thread_id, 64) or None,
                    route=_trim(route, 16) or None,
                    call_point=_trim(call_point, 16) or CALL_POINT_UNKNOWN,
                    model=_trim(model, 64),
                    prompt_tokens=int(prompt_tokens or 0),
                    completion_tokens=int(completion_tokens or 0),
                    total_tokens=int(total_tokens or 0),
                    status=_trim(status, 8) or "success",
                )
            )
    except Exception:  # noqa: BLE001 — 计量旁路：落库失败绝不阻断对话
        return False
    return True


class UsageCallbackHandler(BaseCallbackHandler):
    """把每次 LLM 调用的 usage 落到 `llm_usage`（成功/失败都记）。"""

    def on_llm_end(self, response: Any, *, tags: list[str] | None = None, **kwargs: Any) -> None:
        try:
            tokens = _extract_usage(response)
            _write_usage(
                **get_usage_ctx(),
                call_point=_resolve_call_point(tags),
                model=_extract_model(response, kwargs),
                status="success",
                **tokens,
            )
        except Exception:  # noqa: BLE001, S110 — 旁路兜底：任何异常都不影响对话
            pass

    def on_llm_error(
        self, error: BaseException, *, tags: list[str] | None = None, **kwargs: Any
    ) -> None:
        try:
            _write_usage(
                **get_usage_ctx(),
                call_point=_resolve_call_point(tags),
                model=_extract_model(None, kwargs),
                status="error",
            )
        except Exception:  # noqa: BLE001, S110 — 旁路兜底
            pass


_handler: UsageCallbackHandler | None = None


def usage_handler() -> UsageCallbackHandler:
    """进程内单例 handler（llm.py 构造期无条件挂载）。"""
    global _handler
    if _handler is None:
        _handler = UsageCallbackHandler()
    return _handler
