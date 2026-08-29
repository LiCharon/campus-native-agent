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

    def update_state(self, cfg, values):
        # 测试替身：无状态，reset 对 FakeGraph 无意义（仅生产 CompiledGraph 需要）
        self.reset_cfgs = getattr(self, "reset_cfgs", [])
        self.reset_cfgs.append((cfg, values))


def test_knowledge_route_invokes_graph_with_user_input():
    entry = FakeEntryGraph(KNOWLEDGE)
    kg = FakeGraph(state={"reply": "校历见教务处网站", "finished": True})
    out = turn(entry, kg, FakeGraph(), THREAD, "校历？")
    assert kg.invoked, "knowledge 路由必须 invoke knowledge_graph"
    args, _ = kg.invoked[0]
    assert args[0]["user_input"] == "校历？"
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
    qg = FakeGraph(
        state={"reply": "空闲教室：301", "finished": True, "tool_calls": ["query_empty_rooms"]}
    )
    out = turn(entry, kg, qg, THREAD, "3号楼下午有空教室吗")
    assert qg.invoked and not kg.invoked
    assert out["route"] == TOOL_QUERY
    assert out["tool_calls"] == ["query_empty_rooms"]
    assert qg.get_state_cfgs[0]["configurable"]["thread_id"] == f"{THREAD}:query"


def test_query_pending_resumes_into_query():
    entry = FakeEntryGraph(HUMAN_HANDOFF)
    kg = FakeGraph()
    qg = FakeGraph(
        state={"reply": "补充后的查询", "finished": True, "tool_calls": []}, pending=True
    )
    out = turn(entry, kg, qg, THREAD, "3号楼")
    assert qg.invoked and not kg.invoked
    args, _ = qg.invoked[0]
    assert isinstance(args[0], Command) and args[0].resume == "3号楼"
    assert out["route"] == TOOL_QUERY


def test_multi_primary_tool_routes_to_query():
    intent = IntentResult(
        intent="multi_intent",
        confidence=0.9,
        primary_intent="tool_query",
        secondary_intents=["knowledge"],
    )
    entry = FakeEntryGraph(MULTI_INTENT, intent=intent)
    qg = FakeGraph(
        state={"reply": "空闲教室：301", "finished": True, "tool_calls": ["query_empty_rooms"]}
    )
    out = turn(entry, FakeGraph(), qg, THREAD, "3号楼有空教室吗？顺便问下校历")
    assert qg.invoked
    assert out["route"] == MULTI_INTENT
    assert "可以继续问我" in out["reply"]


def test_multi_primary_other_takes_secondary_with_polite_prefix():
    intent = IntentResult(
        intent="multi_intent",
        confidence=0.9,
        primary_intent="other",
        secondary_intents=["knowledge"],
    )
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


def test_two_consecutive_new_questions_not_swallowed(db_session_factory):
    """M12 B1：orchestrator 非挂起分支 invoke 前 update_state 重置 _consumed，
    同 thread 两轮独立问题都不应被吞（验证 reset 是生产修复主路径）。"""
    from conftest import FakeToolLLM
    from langgraph.checkpoint.memory import InMemorySaver

    from campus_desk.db.models import KnowledgeEntry
    from campus_desk.entry.entry_graph import build_entry_graph
    from campus_desk.entry.intent import IntentResult
    from campus_desk.knowledge.decide import ClarifyDecider
    from campus_desk.knowledge.graph import build_knowledge_graph
    from campus_desk.query.graph import build_query_graph

    class KClassifier:
        def classify(self, user_input, recent=None):
            return IntentResult(intent="knowledge", confidence=0.9, secondary_intents=[], reason="t")

    with db_session_factory() as s, s.begin():
        s.query(KnowledgeEntry).delete()
        s.add(KnowledgeEntry(domain="教务", keywords="校历,寒假", question="放寒假", type="info", answer="寒假以通知为准。"))
        s.add(KnowledgeEntry(domain="图书馆", keywords="开放时间,座位", question="图书馆座位", type="info", answer="目前有空余座位。"))

    entry = build_entry_graph(classifier=KClassifier())
    kg = build_knowledge_graph(db_session_factory, decider=ClarifyDecider(), checkpointer=InMemorySaver())
    qg = build_query_graph(db_session_factory, llm=FakeToolLLM([]), checkpointer=InMemorySaver())
    out1 = turn(entry, kg, qg, "t-consec-o", "什么时候放寒假")
    assert "寒假" in out1["reply"]
    out2 = turn(entry, kg, qg, "t-consec-o", "图书馆还有座位吗")
    assert "座位" in out2["reply"]
