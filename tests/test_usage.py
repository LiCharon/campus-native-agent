"""M13-ZJUT LLM 计量埋点单测：纯函数 / 落库 / handler / 归属上下文 / 调用点标记。

覆盖口径（计划 §6 验收标准）：
- `_extract_usage` 各形态（llm_output / generation_info / 缺失 / total 推导）
- `_write_usage` 落库内容 + 超长截断 + 无数据源返回 False 不抛
- handler：on_llm_end 写 success 行、on_llm_error 写 error 行（直接调方法，不依赖真 LLM）
- `llm.py` 构造后 callbacks 含 usage handler（langfuse 关闭时仍挂）
- 3 个调用点（intent/decide/tool_select）确实打了标记
- orchestrator.turn 调用期间 ctx 有值、结束后清理
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from langchain_core.outputs import Generation, LLMResult

from campus_desk import usage
from campus_desk.config import settings
from campus_desk.db.models import LLMUsage
from campus_desk.entry.intent import IntentClassifier
from campus_desk.entry.orchestrator import turn
from campus_desk.knowledge.decide import ClarifyDecider
from campus_desk.llm import build_llm, build_tool_llm
from campus_desk.query.graph import _call_tools

_INTENT_JSON = '{"intent":"knowledge","confidence":0.9,"secondary_intents":[],"reason":"t"}'


class _FakeLLM:
    """最小化 Fake：只需 invoke 返回带 .content 的对象；bind_tools 返回自身。"""

    def __init__(self, content: str = _INTENT_JSON):
        self.content = content

    def invoke(self, messages, **kwargs):
        return type("Msg", (), {"content": self.content})()

    def bind_tools(self, schemas):
        return self


def _resp(llm_output=None, generation_info=None) -> LLMResult:
    return LLMResult(
        generations=[[Generation(text="hi", generation_info=generation_info or {})]],
        llm_output=llm_output,
    )


@pytest.fixture
def usage_db(db_session_factory):
    """把计量落到测试的 SQLite 内存库（用后恢复默认工厂，防污染其他测试）。"""
    usage.configure(db_session_factory)
    yield db_session_factory
    usage.configure(None)


@pytest.fixture
def spy_call_point(monkeypatch):
    """记录 call_point 调用（包装真实现，语义不变）。"""
    calls: list[str] = []
    real = usage.call_point

    @contextmanager
    def _spy(name):
        calls.append(name)
        with real(name):
            yield

    monkeypatch.setattr(usage, "call_point", _spy)
    return calls


def _rows(factory):
    with factory() as session, session.begin():
        return session.query(LLMUsage).all()


class TestExtractUsage:
    def test_from_llm_output_token_usage(self):
        resp = _resp(
            llm_output={
                "token_usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
            }
        )
        assert usage._extract_usage(resp) == {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        }

    def test_from_generation_info(self):
        resp = _resp(
            generation_info={
                "token_usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
            }
        )
        assert usage._extract_usage(resp)["total_tokens"] == 10

    def test_missing_usage_returns_zero(self):
        assert usage._extract_usage(_resp()) == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def test_total_derived_when_absent(self):
        resp = _resp(llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        assert usage._extract_usage(resp)["total_tokens"] == 15

    def test_none_response_zero(self):
        assert usage._extract_usage(None)["total_tokens"] == 0


class TestExtractModel:
    def test_from_invocation_params(self):
        assert usage._extract_model(None, {"invocation_params": {"model": "deepseek-x"}}) == (
            "deepseek-x"
        )

    def test_from_llm_output_model_name(self):
        assert usage._extract_model(_resp(llm_output={"model_name": "m-2"})) == "m-2"

    def test_fallback_to_settings(self):
        assert usage._extract_model(_resp()) == settings.deepseek_model


class TestCallPoint:
    def test_tag_takes_priority(self):
        assert usage._resolve_call_point(["call_point:decide"]) == "decide"

    def test_contextvar_fallback(self):
        with usage.call_point("tool_select"):
            assert usage._resolve_call_point() == "tool_select"
            assert usage._resolve_call_point(["call_point:intent"]) == "intent"  # tag 优先

    def test_unknown_when_nothing(self):
        assert usage._resolve_call_point() == usage.CALL_POINT_UNKNOWN
        assert usage._resolve_call_point(["other:tag"]) == usage.CALL_POINT_UNKNOWN

    def test_reset_after_exit(self):
        with usage.call_point("intent"):
            pass
        assert usage._resolve_call_point() == usage.CALL_POINT_UNKNOWN


class TestUsageCtx:
    def test_set_patch_clear(self):
        usage.clear_usage_ctx()
        usage.set_usage_ctx(user_id="u1", thread_id="t1")
        usage.patch_usage_ctx(route="knowledge")
        assert usage.get_usage_ctx() == {"user_id": "u1", "thread_id": "t1", "route": "knowledge"}
        usage.clear_usage_ctx()
        assert usage.get_usage_ctx() == {"user_id": None, "thread_id": None, "route": None}

    def test_ctx_manager_clears_on_exit(self):
        with usage.usage_ctx(user_id="u2", thread_id="t2"):
            assert usage.get_usage_ctx()["user_id"] == "u2"
        assert usage.get_usage_ctx()["user_id"] is None


class TestWriteUsage:
    def test_writes_row_with_expected_fields(self, usage_db):
        assert usage._write_usage(
            user_id="student-001",
            thread_id="th-1",
            route="knowledge",
            call_point="decide",
            model="deepseek-test",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        )
        rows = _rows(usage_db)
        assert len(rows) == 1
        row = rows[0]
        assert (row.user_id, row.thread_id, row.route, row.call_point) == (
            "student-001",
            "th-1",
            "knowledge",
            "decide",
        )
        assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (100, 20, 120)
        assert row.status == "success"
        assert row.model == "deepseek-test"

    def test_error_row_has_zero_tokens(self, usage_db):
        usage._write_usage(call_point="intent", status="error")
        row = _rows(usage_db)[0]
        assert row.status == "error"
        assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (0, 0, 0)
        assert row.call_point == "intent"
        assert row.user_id is None  # 无归属 → 空，不是空串

    def test_trims_overlong_fields(self, usage_db):
        usage._write_usage(user_id="u" * 50, call_point="c" * 40, model="m" * 200)
        row = _rows(usage_db)[0]
        assert len(row.user_id) == 32
        assert len(row.call_point) == 16
        assert len(row.model) == 64

    def test_returns_false_without_datasource(self, monkeypatch):
        monkeypatch.setattr(settings, "database_url", "")
        monkeypatch.setattr(usage, "_factory_cache", None)
        usage.configure(None)
        assert usage._write_usage(call_point="intent") is False  # 不抛，静默放弃


class TestUsageHandler:
    def test_on_llm_end_writes_success_row(self, usage_db):
        handler = usage.UsageCallbackHandler()
        with usage.usage_ctx(user_id="u1", thread_id="t1", route="knowledge"):
            handler.on_llm_end(
                _resp(
                    llm_output={
                        "token_usage": {
                            "prompt_tokens": 50,
                            "completion_tokens": 10,
                            "total_tokens": 60,
                        },
                        "model_name": "m1",
                    }
                ),
                tags=["call_point:decide"],
            )
        row = _rows(usage_db)[0]
        assert (row.call_point, row.model, row.status) == ("decide", "m1", "success")
        assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (50, 10, 60)
        assert (row.user_id, row.thread_id, row.route) == ("u1", "t1", "knowledge")

    def test_on_llm_error_writes_error_row(self, usage_db):
        handler = usage.UsageCallbackHandler()
        with usage.usage_ctx(user_id="u2"):
            handler.on_llm_error(RuntimeError("boom"), tags=["call_point:intent"])
        row = _rows(usage_db)[0]
        assert (row.status, row.call_point, row.user_id) == ("error", "intent", "u2")
        assert row.total_tokens == 0

    def test_call_point_from_contextvar(self, usage_db):
        handler = usage.UsageCallbackHandler()
        with usage.call_point(usage.CALL_POINT_TOOL_SELECT):
            handler.on_llm_end(_resp(llm_output={"token_usage": {"prompt_tokens": 1}}))
        assert _rows(usage_db)[0].call_point == "tool_select"

    def test_handler_singleton(self):
        assert usage.usage_handler() is usage.usage_handler()


class TestLLMConstruction:
    def test_build_llm_callbacks_include_usage_handler(self):
        # conftest 已清空 LANGFUSE key → callbacks 里只应有 usage handler
        callbacks = build_llm().callbacks
        assert any(isinstance(c, usage.UsageCallbackHandler) for c in callbacks or [])

    def test_build_tool_llm_callbacks_include_usage_handler(self):
        callbacks = build_tool_llm().callbacks
        assert any(isinstance(c, usage.UsageCallbackHandler) for c in callbacks or [])

    def test_usage_handler_mounted_without_langfuse(self, monkeypatch):
        monkeypatch.setattr(settings, "langfuse_public_key", "")
        monkeypatch.setattr(settings, "langfuse_secret_key", "")
        callbacks = build_llm().callbacks or []
        assert not [c for c in callbacks if "langfuse" in type(c).__module__]
        assert any(isinstance(c, usage.UsageCallbackHandler) for c in callbacks)


class TestCallPointTagging:
    def test_intent_call_tagged(self, spy_call_point):
        IntentClassifier(llm=_FakeLLM()).classify("什么时候放寒假？")
        assert usage.CALL_POINT_INTENT in spy_call_point

    def test_decide_call_tagged(self, spy_call_point):
        ClarifyDecider(llm=_FakeLLM('{"action":"handoff","reply":"转人工"}')).decide(
            history=[], user_text="怎么办", missed=True
        )
        assert usage.CALL_POINT_DECIDE in spy_call_point

    def test_tool_select_call_tagged(self, spy_call_point):
        deps = SimpleNamespace(llm=_FakeLLM(), query_prompt=lambda: "prompt")
        _call_tools(deps, "明天有空教室吗")
        assert usage.CALL_POINT_TOOL_SELECT in spy_call_point


class TestOrchestratorCtx:
    class _SpyEntryGraph:
        """记录 invoke 期间的归属上下文，返回兜底路由（不进知识/工具图）。"""

        def __init__(self):
            self.seen = None

        def invoke(self, payload):
            self.seen = usage.get_usage_ctx()
            return {"route": "human_handoff"}

    class _StubGraph:
        """知识/工具图占位：只需支持挂起判定（本例均无挂起）。"""

        def get_state(self, cfg):
            return SimpleNamespace(next=())

    def test_turn_sets_ctx_during_call_and_clears_after(self):
        entry = self._SpyEntryGraph()
        turn(
            entry,
            self._StubGraph(),
            self._StubGraph(),
            "th-1",
            "你好",
            user_id="student-001",
        )
        assert entry.seen == {"user_id": "student-001", "thread_id": "th-1", "route": None}
        assert usage.get_usage_ctx() == {"user_id": None, "thread_id": None, "route": None}
