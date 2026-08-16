"""Orchestrator 编排测试（M2-T9）：Entry 分流 → KnowledgeGraph / QueryGraph（三图）。

用 Fake 图（记录 invoke/get_state 调用、返回预设 state）断言：
- knowledge 路由 → knowledge_graph 被 invoke（新会话 {"user_input": msg}）
- tool_query 路由 → query_graph 被 invoke（thread_id 派生 :query）
- 挂起中（get_state().next 非空）→ Command(resume=msg) 恢复；query 挂起优先
- multi_intent 按 primary_intent 路由（tool/knowledge/other 取 secondary/缺失兜底 + 礼貌前缀）
- hits 透传
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


class FakeGraph:
    """记录 invoke 调用与 get_state 的 cfg；get_state 返回可配 next。"""

    def __init__(self, state: dict | None = None, pending: bool = False):
        self.state = state or {"reply": "回答", "finished": True}
        self.pending = pending
        self.invoked = []  # [(args, kwargs)]
        self.get_state_cfgs = []

    def get_state(self, cfg):
        self.get_state_cfgs.append(cfg)

        class _S:
            next = ("collect",) if self.pending else ()

        return _S()

    def invoke(self, *args, **kwargs):
        self.invoked.append((args, kwargs))
        return self.state


def test_knowledge_route_invokes_graph_with_user_input():
    entry = FakeEntryGraph(KNOWLEDGE)
    kg = FakeGraph(state={"reply": "校历见教务处网站", "finished": True})
    out = turn(entry, kg, FakeGraph(), THREAD, "校历？")
    assert kg.invoked, "knowledge 路由必须 invoke knowledge_graph"
    args, _ = kg.invoked[0]
    assert args[0] == {"user_input": "校历？"}
    assert out["route"] == KNOWLEDGE
    assert out["reply"] == "校历见教务处网站"


def test_pending_knowledge_resumes_with_command():
    entry = FakeEntryGraph(KNOWLEDGE)
    kg = FakeGraph(state={"reply": "补充后的回答", "finished": True}, pending=True)
    out = turn(entry, kg, FakeGraph(), THREAD, "寒假")
    assert kg.invoked
    args, _ = kg.invoked[0]
    assert isinstance(args[0], Command)
    assert args[0].resume == "寒假"
    assert out["route"] == KNOWLEDGE


def test_pending_human_handoff_resumes_into_knowledge():
    # knowledge 挂起中，任何输入（即使判为 other/低置信→human_handoff）都 resume 进知识图
    entry = FakeEntryGraph(HUMAN_HANDOFF)
    kg = FakeGraph(state={"reply": "追问后的回答", "finished": True}, pending=True)
    out = turn(entry, kg, FakeGraph(), THREAD, "就是图书馆那个")
    assert kg.invoked
    args, _ = kg.invoked[0]
    assert isinstance(args[0], Command) and args[0].resume == "就是图书馆那个"
    assert out["route"] == KNOWLEDGE


def test_tool_query_routes_to_query_graph_with_derived_thread():
    entry = FakeEntryGraph(TOOL_QUERY)
    kg = FakeGraph()
    qg = FakeGraph(state={"reply": "空闲教室：301", "finished": True,
                          "tool_calls": ["query_empty_rooms"]})
    out = turn(entry, kg, qg, THREAD, "3号楼下午有空教室吗")
    assert qg.invoked and not kg.invoked
    assert out["route"] == TOOL_QUERY
    assert out["tool_calls"] == ["query_empty_rooms"]
    assert qg.get_state_cfgs[0]["configurable"]["thread_id"] == f"{THREAD}:query"


def test_query_pending_resumes_into_query():
    entry = FakeEntryGraph(HUMAN_HANDOFF)
    kg = FakeGraph()
    qg = FakeGraph(state={"reply": "补充后的查询", "finished": True, "tool_calls": []}, pending=True)
    out = turn(entry, kg, qg, THREAD, "3号楼")
    assert qg.invoked and not kg.invoked
    args, _ = qg.invoked[0]
    assert isinstance(args[0], Command) and args[0].resume == "3号楼"
    assert out["route"] == TOOL_QUERY


def test_multi_primary_tool_routes_to_query():
    intent = IntentResult(intent="multi_intent", confidence=0.9, primary_intent="tool_query",
                          secondary_intents=["knowledge"])
    entry = FakeEntryGraph(MULTI_INTENT, intent=intent)
    qg = FakeGraph(state={"reply": "空闲教室：301", "finished": True,
                          "tool_calls": ["query_empty_rooms"]})
    out = turn(entry, FakeGraph(), qg, THREAD, "3号楼有空教室吗？顺便问下校历")
    assert qg.invoked
    assert out["route"] == MULTI_INTENT
    assert "可以继续问我" in out["reply"]


def test_multi_primary_other_takes_secondary_with_polite_prefix():
    intent = IntentResult(intent="multi_intent", confidence=0.9, primary_intent="other",
                          secondary_intents=["knowledge"])
    entry = FakeEntryGraph(MULTI_INTENT, intent=intent)
    kg = FakeGraph(state={"reply": "寒假以通知为准。", "finished": True})
    out = turn(entry, kg, FakeGraph(), THREAD, "今天天气怎么样？顺便问下校历")
    assert kg.invoked
    assert out["reply"].startswith("您好！")
    assert "可以继续问我" in out["reply"]


def test_multi_primary_missing_defaults_knowledge():
    intent = IntentResult(intent="multi_intent", confidence=0.9)
    entry = FakeEntryGraph(MULTI_INTENT, intent=intent)
    kg = FakeGraph(state={"reply": "知识库回答", "finished": True})
    turn(entry, kg, FakeGraph(), THREAD, "两个问题")
    assert kg.invoked


def test_knowledge_hits_passthrough():
    entry = FakeEntryGraph(KNOWLEDGE)
    kg = FakeGraph(state={"reply": "命中", "finished": True, "outcome": "answer", "hits": [1, 4]})
    out = turn(entry, kg, FakeGraph(), THREAD, "什么时候放寒假？")
    assert out["hits"] == [1, 4]
