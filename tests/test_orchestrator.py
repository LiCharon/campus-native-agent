"""Orchestrator 编排测试（M1-T7）：Entry 分流 → KnowledgeGraph（或占位回复）。

用 Fake 图（记录 invoke 调用/返回预设 state）断言：
- knowledge 路由 → knowledge_graph 被 invoke（新会话 {"user_input": msg}）
- knowledge_graph 挂起中（get_state().next 非空）→ Command(resume=msg)
- 挂起中 human_handoff 也 resume 进 knowledge_graph（M3 同源坑：追问不能被当新话题）
- tool_query → 占位回复不 invoke knowledge_graph
- multi_intent → invoke + reply 带次要提示
"""

from langgraph.types import Command

from campus_desk.entry.intent import IntentResult
from campus_desk.entry.orchestrator import turn
from campus_desk.entry.routes import (
    HUMAN_HANDOFF,
    KNOWLEDGE,
    MULTI_INTENT,
    TOOL_QUERY,
)

THREAD = "t-1"


class FakeEntryGraph:
    """记录 invoke 输入，按预设返回路由结果。"""

    def __init__(self, route, intent: IntentResult | None = None):
        self.route = route
        self.intent = intent
        self.invoked_with = None

    def invoke(self, payload):
        self.invoked_with = payload
        out = {"route": self.route}
        if self.intent is not None:
            out["intent"] = self.intent
        return out


class FakeKnowledgeGraph:
    """记录 invoke 调用，get_state 返回可配 next，invoke 返回预设 state。"""

    def __init__(self, state: dict | None = None, pending: bool = False):
        self.state = state or {"reply": "知识库回答", "finished": True}
        self.pending = pending
        self.invoked = []  # [(args, kwargs)]

    def get_state(self, cfg):
        class _S:
            next = ("collect",) if self.pending else ()

        return _S()

    def invoke(self, *args, **kwargs):
        self.invoked.append((args, kwargs))
        return self.state


def test_knowledge_route_invokes_graph_with_user_input():
    entry = FakeEntryGraph(KNOWLEDGE)
    kg = FakeKnowledgeGraph(state={"reply": "校历见教务处网站", "finished": True})
    out = turn(entry, kg, THREAD, "校历？")
    assert kg.invoked, "knowledge 路由必须 invoke knowledge_graph"
    args, _ = kg.invoked[0]
    assert args[0] == {"user_input": "校历？"}
    assert out["route"] == KNOWLEDGE
    assert out["reply"] == "校历见教务处网站"


def test_pending_knowledge_resumes_with_command():
    entry = FakeEntryGraph(KNOWLEDGE)
    kg = FakeKnowledgeGraph(state={"reply": "补充后的回答", "finished": True}, pending=True)
    out = turn(entry, kg, THREAD, "寒假")
    assert kg.invoked, "挂起中必须 resume 进 knowledge_graph"
    args, _ = kg.invoked[0]
    assert isinstance(args[0], Command)
    assert args[0].resume == "寒假"
    assert out["route"] == KNOWLEDGE


def test_pending_human_handoff_resumes_into_knowledge():
    # M3 同源坑：knowledge 挂起中，任何输入（即使判为 other/低置信→human_handoff）
    # 都 resume 进 KnowledgeGraph，不落到人工占位
    entry = FakeEntryGraph(HUMAN_HANDOFF)
    kg = FakeKnowledgeGraph(state={"reply": "追问后的回答", "finished": True}, pending=True)
    out = turn(entry, kg, THREAD, "就是图书馆那个")
    assert kg.invoked, "挂起中 human_handoff 必须 resume 进 knowledge_graph"
    args, _ = kg.invoked[0]
    assert isinstance(args[0], Command)
    assert args[0].resume == "就是图书馆那个"
    assert out["route"] == KNOWLEDGE  # 路由归为 knowledge，不是 human_handoff


def test_tool_query_placeholder_no_knowledge_invoke():
    entry = FakeEntryGraph(TOOL_QUERY)
    kg = FakeKnowledgeGraph()
    out = turn(entry, kg, THREAD, "有空教室吗")
    assert not kg.invoked, "tool_query 不 invoke knowledge_graph"
    assert out["route"] == TOOL_QUERY
    assert "建设中" in out["reply"]


def test_multi_intent_invokes_and_appends_secondary_prompt():
    intent = IntentResult(
        intent="multi_intent", confidence=0.9, secondary_intents=["knowledge"]
    )
    entry = FakeEntryGraph(MULTI_INTENT, intent=intent)
    kg = FakeKnowledgeGraph(state={"reply": "已回答主问题", "finished": True})
    out = turn(entry, kg, THREAD, "成绩单怎么打？顺便问校历")
    assert kg.invoked, "multi_intent 必须 invoke knowledge_graph"
    assert out["route"] == MULTI_INTENT
    assert "已回答主问题" in out["reply"]
    assert "可以继续问我" in out["reply"]
