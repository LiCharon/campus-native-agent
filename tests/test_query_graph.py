"""QueryGraph 测试（M2）：collect→wait ping-pong 工具查询图。

覆盖：FC 直查 / 缺楼栋追问→补全后合并重查 / 规则兜底字段齐直接查表 /
工具失败降级→熔断→转人工（四层失败链）/ 追问超限强制 handoff / 图书馆空参数工具。
全部注入 FakeToolLLM，不依赖真 LLM。
"""

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from campus_desk.query.graph import MAX_CLARIFY_ROUNDS, build_query_graph


def rooms_call(building="3号楼", period="下午"):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "query_empty_rooms", "args": {"building": building, "period": period},
             "id": "call_1", "type": "tool_call"}
        ],
    )


def library_call():
    return AIMessage(content="", tool_calls=[
        {"name": "query_library_seats", "args": {}, "id": "call_2", "type": "tool_call"}
    ])


def empty():
    return AIMessage(content="", tool_calls=[])


def make(factory, llm, thread="t-q"):
    graph = build_query_graph(factory, llm=llm, checkpointer=InMemorySaver())
    return graph, {"configurable": {"thread_id": thread}}


def test_fc_tool_call_answers(db_session_factory):
    from conftest import FakeToolLLM

    graph, cfg = make(db_session_factory, FakeToolLLM([rooms_call()]))
    out = graph.invoke({"user_input": "3号楼下午有空教室吗"}, cfg)
    assert out["finished"] is True
    assert out["outcome"] == "answer"
    assert out["tool_calls"] == ["query_empty_rooms"]
    assert "空闲教室" in out["reply"]


def test_missing_building_asks_then_answers(db_session_factory):
    from conftest import FakeToolLLM

    fake = FakeToolLLM([empty(), empty(), rooms_call("3号楼", "下午")])
    graph, cfg = make(db_session_factory, fake)
    first = graph.invoke({"user_input": "有空教室吗"}, cfg)
    assert first["finished"] is False and first["outcome"] == "ask"
    assert "楼" in first["pending_question"]
    second = graph.invoke(Command(resume="3号楼，下午"), cfg)
    assert second["finished"] is True and second["outcome"] == "answer"
    assert second["tool_calls"] == ["query_empty_rooms"]


def test_rule_fallback_direct_query_when_fields_complete(db_session_factory):
    from conftest import FakeToolLLM

    fake = FakeToolLLM([empty(), empty()])  # FC 两次都失败 → 规则抽取兜底
    graph, cfg = make(db_session_factory, fake)
    out = graph.invoke({"user_input": "3号楼晚上有空教室吗"}, cfg)
    assert out["finished"] is True and out["outcome"] == "answer"
    assert out["tool_calls"] == ["query_empty_rooms"]
    assert "晚上" in out["reply"]


def test_library_tool_call_answers(db_session_factory):
    from conftest import FakeToolLLM

    graph, cfg = make(db_session_factory, FakeToolLLM([library_call()]))
    out = graph.invoke({"user_input": "图书馆现在有座位吗"}, cfg)
    assert out["finished"] is True and out["outcome"] == "answer"
    assert out["tool_calls"] == ["query_library_seats"]
    assert "空余座位" in out["reply"]


def test_tool_failure_degrades_then_circuit_handoffs(db_session_factory):
    from conftest import FakeToolLLM

    class BrokenFactory:
        """第一次调用（工具查询）抛异常；后续调用（bad_case 写入）走真库。"""

        def __init__(self, real):
            self.real = real
            self.tool_calls = 0

        def __call__(self):
            self.tool_calls += 1
            if self.tool_calls == 1:
                raise RuntimeError("db down")
            return self.real()

    fake = FakeToolLLM([rooms_call()])
    graph, cfg = make(BrokenFactory(db_session_factory), fake)
    one = graph.invoke({"user_input": "3号楼下午有空教室吗"}, cfg)
    assert one["outcome"] == "degraded"
    two = graph.invoke({"user_input": "还是查不到吗"}, cfg)
    assert two["outcome"] == "degraded" and fake.calls == 1
    three = graph.invoke({"user_input": "再试一次"}, cfg)
    assert three["outcome"] == "handoff"
    from campus_desk.db.models import BadCase

    with db_session_factory() as s:
        assert s.query(BadCase).count() == 1


def test_max_clarify_rounds_forced_handoff(db_session_factory):
    from conftest import FakeToolLLM

    from campus_desk.db.models import BadCase

    graph, cfg = make(db_session_factory, FakeToolLLM([]))
    out = graph.invoke({"user_input": "有空教室吗"}, cfg)
    assert out["finished"] is False
    for _ in range(MAX_CLARIFY_ROUNDS):
        out = graph.invoke(Command(resume="不知道"), cfg)
    assert out["finished"] is True and out["outcome"] == "handoff"
    with db_session_factory() as s:
        assert s.query(BadCase).one().status == "PENDING"
