"""每轮编排（M1-ZJUT）：Entry 分流 → KnowledgeGraph（或占位回复）。

thread_id 由调用方管理（前端会话 id；评测用 case id）。

T7 Minor：多意图次要提示 labels 转中文（复用 entry_graph._INTENT_LABELS）；
占位文案单一来源（复用 entry_graph._ROUTE_REPLIES，删本地重复定义）。
"""

from langgraph.types import Command

from campus_desk import telemetry
from campus_desk.entry.entry_graph import _INTENT_LABELS, _ROUTE_REPLIES
from campus_desk.entry.routes import HUMAN_HANDOFF, KNOWLEDGE, MULTI_INTENT


def turn(entry_graph, knowledge_graph, thread_id: str, msg: str, *, user_id: str | None = None) -> dict:
    """一轮对话：Entry 分流 → 按路由进 KnowledgeGraph（或占位回复）。"""
    with (
        telemetry.trace_attrs(user_id=user_id, session_id=thread_id, tags=["zjut-m1"]),
        telemetry.span("orchestrator.turn", metadata={"thread_id": thread_id}),
    ):
        entry_out = entry_graph.invoke({"user_input": msg})
        route = entry_out["route"]
        cfg = {"configurable": {"thread_id": thread_id}}
        pending = knowledge_graph.get_state(cfg).next != ()

        if route == KNOWLEDGE:
            with telemetry.span("agent.knowledge", metadata={"thread_id": thread_id}):
                state = knowledge_graph.invoke(Command(resume=msg), cfg) if pending \
                    else knowledge_graph.invoke({"user_input": msg}, cfg)
            return {
                "reply": state.get("reply", ""),
                "route": KNOWLEDGE,
                "pending_question": state.get("pending_question"),
                "finished": state.get("finished"),
                "outcome": state.get("outcome"),
            }
        if route == MULTI_INTENT:
            primary = entry_out.get("intent")
            secondary = primary and primary.secondary_intents or []
            with telemetry.span("agent.knowledge", metadata={"thread_id": thread_id}):
                state = knowledge_graph.invoke(Command(resume=msg), cfg) if pending \
                    else knowledge_graph.invoke({"user_input": msg}, cfg)
            reply = state.get("reply", "")
            if secondary:
                labels = "、".join(_INTENT_LABELS.get(s, s) for s in secondary)
                reply += f" 另外，您提到的其他问题（{labels}）可以继续问我。"
            return {
                "reply": reply,
                "route": MULTI_INTENT,
                "pending_question": state.get("pending_question"),
                "finished": state.get("finished"),
                "outcome": state.get("outcome"),
            }
        # knowledge 挂起中任何输入都 resume 进 KnowledgeGraph（M3 同源坑：追问的回答不能被当新话题）
        if pending and route == HUMAN_HANDOFF:
            with telemetry.span("agent.knowledge", metadata={"thread_id": thread_id}):
                state = knowledge_graph.invoke(Command(resume=msg), cfg)
            return {
                "reply": state.get("reply", ""),
                "route": KNOWLEDGE,
                "pending_question": state.get("pending_question"),
                "finished": state.get("finished"),
                "outcome": state.get("outcome"),
            }
        return {
            "reply": _ROUTE_REPLIES.get(route, entry_out.get("reply", "")),
            "route": route,
            "secondary_intents": entry_out.get("intent", None)
            and entry_out["intent"].secondary_intents or [],
        }
