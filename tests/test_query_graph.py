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
            {
                "name": "query_empty_rooms",
                "args": {"building": building, "period": period},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )


def library_call():
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "query_library_seats", "args": {}, "id": "call_2", "type": "tool_call"}
        ],
    )


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
        """前 3 次会话抛异常（图构建期 lookup_student_no + prompt 校历查询 + 工具查询），
        后续调用（bad_case 写入）走真库。M2+ 新增前两个 DB 调用，配额随之调整。"""

        def __init__(self, real):
            self.real = real
            self.tool_calls = 0

        def __call__(self):
            self.tool_calls += 1
            if self.tool_calls <= 3:
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


# ---- M2+ FC 扩展测试 ----

def timetable_call(week=6, weekday=3):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "query_timetable",
                "args": {"week": week, "weekday": weekday, "hallucinated": "x"},
                "id": "call_t",
                "type": "tool_call",
            }
        ],
    )


def power_call(building="3号楼", room="205"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "query_dorm_power",
                "args": {"building": building, "room": room},
                "id": "call_p",
                "type": "tool_call",
            }
        ],
    )


def test_dynamic_whitelist_filters_hallucinated_args(db_session_factory):
    """白名单从 schema required 派生：LLM 传多余 key 被过滤，不传给工具。"""
    from conftest import FakeToolLLM

    graph, cfg = make(db_session_factory, FakeToolLLM([timetable_call()]))
    out = graph.invoke({"user_input": "这周三上午我有啥课"}, cfg)
    assert out["finished"] is True and out["outcome"] == "answer"
    assert out["tool_calls"] == ["query_timetable"]
    assert "操作系统" in out["reply"] and "第 6 周" in out["reply"]


def test_student_no_injected_for_personal_tool(db_session_factory):
    """_run_tool 注入 deps.student_no：无需 LLM 传学号，个人工具直接出数据。"""
    from conftest import FakeToolLLM

    fake = FakeToolLLM([timetable_call()])
    graph = build_query_graph(
        db_session_factory, llm=fake, checkpointer=InMemorySaver(), student_no="2024001"
    )
    cfg = {"configurable": {"thread_id": "t-inject"}}
    out = graph.invoke({"user_input": "这周三上午我有啥课"}, cfg)
    assert out["outcome"] == "answer"
    assert "操作系统" in out["reply"]


def test_clarify_routes_to_dorm_power(db_session_factory):
    """缺参追问按领域路由：电量 → 宿舍楼栋房间追问（只问不答）。"""
    from conftest import FakeToolLLM

    fake = FakeToolLLM([empty(), empty(), power_call()])
    graph, cfg = make(db_session_factory, fake)
    out = graph.invoke({"user_input": "我们宿舍还有多少电"}, cfg)
    assert out["finished"] is False and out["outcome"] == "ask"
    assert "宿舍" in out["pending_question"] and "房间" in out["pending_question"]
    second = graph.invoke(Command(resume="3号楼 205"), cfg)
    assert second["finished"] is True and second["outcome"] == "answer"
    assert second["tool_calls"] == ["query_dorm_power"]
    assert "剩余电量" in second["reply"]


def test_time_context_injected_into_prompt(db_session_factory):
    """prompt 注入时间上下文：today=2026-10-15 → 第 6 周/2026-2027-1。"""
    from datetime import date

    from conftest import FakeToolLLM

    class RecordingLLM(FakeToolLLM):
        def __init__(self):
            super().__init__([empty()])
            self.last_system = ""

        def invoke(self, messages):
            self.last_system = messages[0][1]
            return super().invoke(messages)

    llm = RecordingLLM()
    graph = build_query_graph(
        db_session_factory,
        llm=llm,
        checkpointer=InMemorySaver(),
        today=date(2026, 10, 15),
    )
    cfg = {"configurable": {"thread_id": "t-time"}}
    graph.invoke({"user_input": "这周三上午我有啥课"}, cfg)
    assert "2026-10-15" in llm.last_system
    assert "第 6 周" in llm.last_system
    assert "2026-2027-1" in llm.last_system
    assert "2025-2026-2" in llm.last_system  # prev_term


def test_fc_failure_degrades_for_new_tool(db_session_factory):
    """新工具失败 → 专属降级文案（DEGRADED_REPLIES 查表）。"""
    from conftest import FakeToolLLM

    class BoomFactory:
        def __init__(self, real):
            self.real = real

        def __call__(self):
            raise RuntimeError("db down")

    fake = FakeToolLLM([power_call()])
    graph, cfg = make(BoomFactory(db_session_factory), fake)
    out = graph.invoke({"user_input": "3号楼205宿舍还有多少电"}, cfg)
    assert out["outcome"] == "degraded"
    assert out["tool_calls"] == ["query_dorm_power"]
    assert "电量" in out["reply"]


def test_clarify_routes_to_timetable_asks_weekday(db_session_factory):
    """课表缺参追问按领域路由：问星期几，而非默认的楼栋追问。"""
    from conftest import FakeToolLLM

    fake = FakeToolLLM([empty(), empty(), timetable_call()])
    graph, cfg = make(db_session_factory, fake)
    out = graph.invoke({"user_input": "查下我的课表"}, cfg)
    assert out["finished"] is False and out["outcome"] == "ask"
    assert ("星期" in out["pending_question"]) or ("周几" in out["pending_question"])
    assert "教学楼" not in out["pending_question"]
    second = graph.invoke(Command(resume="周三"), cfg)
    assert second["finished"] is True and second["outcome"] == "answer"
    assert second["tool_calls"] == ["query_timetable"]
