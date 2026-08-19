"""Langfuse 埋点测试（M5-T3）：无 key 全禁用、零副作用、惰性 import 锁死。

关键设计（锁测试）：
- conftest autouse fixture 已清空 LANGFUSE 双 key → 本文件全部走无 key 路径
- sys.modules 断言"langfuse 未被 import"——锁惰性 import 设计：
  span/trace_attrs/flush/langfuse_handler 在 enabled()=False 时不得触发包加载
"""

import sys

import pytest

from campus_desk import telemetry
from campus_desk.config import settings
from campus_desk.llm import build_llm


def test_enabled_false_without_keys():
    """无 key（conftest 已清空）时埋点开关必须为 False。"""
    assert telemetry.enabled() is False


def test_span_noop_without_keys():
    """span() 无 key 时纯 no-op：不抛错、yield None、不吞异常。"""
    with telemetry.span("orchestrator.turn", metadata={"ticket_id": 1, "actor": "x"}) as s:
        assert s is None
    # 异常穿过 span（埋点不得吞业务异常）
    with pytest.raises(ValueError), telemetry.span("agent.repair"):
        raise ValueError("x")


def test_trace_attrs_noop_without_keys():
    """trace_attrs() 无 key 时纯 no-op：不抛错、yield None。"""
    with telemetry.trace_attrs(user_id="u", session_id="s", tags=["t"], trace_name="n") as _:
        pass


def test_build_llm_without_callbacks():
    """build_llm() 无 key 时不挂 callbacks（llm.callbacks 为 None）。"""
    llm = build_llm()
    assert llm.callbacks is None
    assert llm.model_kwargs == {"response_format": {"type": "json_object"}}  # 结构化模板不动


def test_langfuse_package_not_imported():
    """全链路埋点调用后 langfuse 包不得被 import（锁惰性 import 设计）。"""
    telemetry.flush()
    with telemetry.span("x"), telemetry.trace_attrs(user_id="u"):
        pass
    assert telemetry.langfuse_handler() is None
    telemetry.score_trace(name="turn.outcome", value=1.0, comment="outcome=answer")
    build_llm()
    assert "langfuse" not in sys.modules


def test_score_trace_noop_without_keys():
    """score_trace 无 key 时纯 no-op：不抛错、不 import langfuse（sys.modules 锁死）。"""
    telemetry.score_trace(name="turn.outcome", value=1.0)
    telemetry.score_trace(name="x", value=0.5, comment="c", config_id="cfg")
    assert "langfuse" not in sys.modules


def test_score_trace_calls_current_trace_when_enabled(monkeypatch):
    """enabled 路径：mock client 的 score_current_trace 被调用（参数透传）。"""
    monkeypatch.setattr(settings, "langfuse_public_key", "pk")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk")
    monkeypatch.setattr(settings, "langfuse_host", "http://localhost:3001")

    calls = {}

    class FakeClient:
        def score_current_trace(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(telemetry, "_client", FakeClient())
    telemetry.score_trace(name="turn.outcome", value=0.6, comment="outcome=ask")
    assert calls["name"] == "turn.outcome"
    assert calls["value"] == 0.6
    assert calls["comment"] == "outcome=ask"
