"""每轮编排（M2）：Entry 分流 → KnowledgeGraph / QueryGraph（三图）。

thread_id 由调用方管理（前端会话 id；评测用 case id）。
两图状态隔离（实测：langgraph 1.2.10 无 checkpoint_ns）→ query 图派生
thread_id "{thread_id}:query"；knowledge 图沿用裸 thread_id（M1 会话兼容）。

挂起恢复语义（设计 §5.3）：任一图追问挂起中，下一轮任何输入都 resume 进
挂起图（追问补充不当新话题）；同一 thread 同时只有一个管道在追问，防御性
按 query 优先判定。

multi_intent 路由（拍板）：primary∈{knowledge,tool_query} 走对应管道；
primary=other/缺失 → 取 secondary 首个非 other 意图（回复加"您好！"前缀
当 primary=other 时）；取不到 → knowledge 兜底（管道自带追问/转人工保护）。
"""

from langgraph.types import Command

from campus_desk import telemetry, usage
from campus_desk.entry.entry_graph import _INTENT_LABELS, _ROUTE_REPLIES
from campus_desk.entry.routes import KNOWLEDGE, MULTI_INTENT, TOOL_QUERY

_POLITE_PREFIX = "您好！"

# M2+：按轮次 outcome 自动评分（方案 §六 满意度/指标聚合；无 key 时 no-op）
_OUTCOME_SCORE = {"answer": 1.0, "ask": 0.6, "degraded": 0.3, "handoff": 0.0}


def _score_outcome(outcome: str | None) -> None:
    """给当前 trace 打 turn.outcome 分（answer 1.0 … handoff 0.0）。"""
    value = _OUTCOME_SCORE.get(outcome)
    if value is not None:
        telemetry.score_trace(name="turn.outcome", value=value, comment=f"outcome={outcome}")


def _scored(result: dict) -> dict:
    _score_outcome(result.get("outcome"))
    return result


def _knowledge_result(state: dict) -> dict:
    # M13：回写最终 route，供 usage 埋点归属（两处 knowledge 出口 + 挂起恢复都过这里）
    usage.patch_usage_ctx(route=KNOWLEDGE)
    return {
        "reply": state.get("reply", ""),
        "route": KNOWLEDGE,
        "pending_question": state.get("pending_question"),
        "finished": state.get("finished"),
        "outcome": state.get("outcome"),
        "hits": state.get("hits", []),
    }


def _query_result(state: dict) -> dict:
    usage.patch_usage_ctx(route=TOOL_QUERY)
    return {
        "reply": state.get("reply", ""),
        "route": TOOL_QUERY,
        "pending_question": state.get("pending_question"),
        "finished": state.get("finished"),
        "outcome": state.get("outcome"),
        "tool_calls": state.get("tool_calls", []),
    }


def _secondary_labels(intent) -> list[str]:
    secondary = [s for s in (intent.secondary_intents if intent else []) if s in _INTENT_LABELS]
    return [_INTENT_LABELS[s] for s in secondary]


def turn(
    entry_graph,
    knowledge_graph,
    query_graph,
    thread_id: str,
    msg: str,
    *,
    user_id: str | None = None,
    recent: list[str] | None = None,
) -> dict:
    """一轮对话：Entry 分流 → 按路由/主意图进 knowledge 或 query 图。

    recent（M12-ZJUT）：近期对话 user 文本（messages 表最近 N 轮，已排除当前消息），
    注入 intent/decide/工具选择理解指代；不进入检索拼接（检索按当前+图内 ≤3 轮）。
    """
    with (
        telemetry.trace_attrs(user_id=user_id, session_id=thread_id, tags=["zjut-m2"]),
        telemetry.span("orchestrator.turn", metadata={"thread_id": thread_id}),
        # M13：归属上下文（user/thread 入口设置，route 由各出口回写）——退出自动清理
        usage.usage_ctx(user_id=user_id, thread_id=thread_id),
    ):
        entry_out = entry_graph.invoke({"user_input": msg, "recent": recent})
        route = entry_out["route"]
        intent = entry_out.get("intent")
        cfg_k = {"configurable": {"thread_id": thread_id}}
        cfg_q = {"configurable": {"thread_id": f"{thread_id}:query"}}
        k_pending = knowledge_graph.get_state(cfg_k).next != ()
        q_pending = query_graph.get_state(cfg_q).next != ()

        # 挂起恢复优先（防御按 query 优先；两图不会同时挂起）
        # M13：route 在 invoke **前**回写——图内 LLM 调用（decide/tool_select）才能带上正确归属
        if q_pending:
            usage.patch_usage_ctx(route=TOOL_QUERY)
            with telemetry.span("agent.query", metadata={"thread_id": thread_id}):
                state = query_graph.invoke(Command(resume=msg), cfg_q)
            return _scored(_query_result(state))
        if k_pending:
            usage.patch_usage_ctx(route=KNOWLEDGE)
            with telemetry.span("agent.knowledge", metadata={"thread_id": thread_id}):
                state = knowledge_graph.invoke(Command(resume=msg), cfg_k)
            return _scored(_knowledge_result(state))

        # 非挂起：按 route 选图，invoke 前 reset 该图残留标记，避免同会话已完成
        # 轮次后 invoke 新问题被持久化的 _consumed=True 吞掉（collect 误判"已处理"
        # 取空 student_answer）。只 reset 本轮要 invoke 的图——reset 后立刻 invoke，
        # state 被真实节点覆盖，不会留下假挂起；另一图本轮不 invoke，不 reset 也
        # 不制造假挂起。挂起分支（Command(resume)）已在上方面 return，不受影响。
        _reset_k = {
            "_consumed": False,
            "history": [],
            "rounds": 0,
            "student_answer": None,
            "pending_question": None,
            "finished": False,
        }
        _reset_q = {**_reset_k, "fail_count": 0, "tool_calls": []}

        if route == KNOWLEDGE:
            usage.patch_usage_ctx(route=KNOWLEDGE)
            with telemetry.span("agent.knowledge", metadata={"thread_id": thread_id}):
                knowledge_graph.update_state(cfg_k, _reset_k)
                state = knowledge_graph.invoke({"user_input": msg, "recent": recent}, cfg_k)
            return _scored(_knowledge_result(state))

        if route == TOOL_QUERY:
            usage.patch_usage_ctx(route=TOOL_QUERY)
            with telemetry.span("agent.query", metadata={"thread_id": thread_id}):
                query_graph.update_state(cfg_q, _reset_q)
                state = query_graph.invoke({"user_input": msg, "recent": recent}, cfg_q)
            return _scored(_query_result(state))

        if route == MULTI_INTENT:
            primary = intent.primary_intent if intent else None
            polite = primary == "other"
            if primary not in (KNOWLEDGE, TOOL_QUERY):
                secondary = [
                    s
                    for s in (intent.secondary_intents if intent else [])
                    if s in (KNOWLEDGE, TOOL_QUERY)
                ]
                primary = secondary[0] if secondary else KNOWLEDGE
            usage.patch_usage_ctx(route=MULTI_INTENT)
            if primary == TOOL_QUERY:
                with telemetry.span("agent.query", metadata={"thread_id": thread_id}):
                    query_graph.update_state(cfg_q, _reset_q)
                    state = query_graph.invoke({"user_input": msg, "recent": recent}, cfg_q)
                result = _query_result(state)
            else:
                with telemetry.span("agent.knowledge", metadata={"thread_id": thread_id}):
                    knowledge_graph.update_state(cfg_k, _reset_k)
                    state = knowledge_graph.invoke({"user_input": msg, "recent": recent}, cfg_k)
                result = _knowledge_result(state)
            result["route"] = MULTI_INTENT
            usage.patch_usage_ctx(route=MULTI_INTENT)
            reply = result["reply"]
            if polite:
                reply = _POLITE_PREFIX + reply
            labels = _secondary_labels(intent)
            if labels:
                reply += f" 另外，您提到的其他问题（{'、'.join(labels)}）可以继续问我。"
            result["reply"] = reply
            return _scored(result)

        # knowledge/query 均未挂起时的兜底（other/低置信 → human_handoff）
        return {
            "reply": _ROUTE_REPLIES.get(route, entry_out.get("reply", "")),
            "route": route,
            "secondary_intents": intent.secondary_intents if intent else [],
        }
