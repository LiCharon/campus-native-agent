"""每轮编排（M3 Repair + M4 Consult/Quality）：Entry 分流 → 下游 Agent 图。

"入口路由处挂下游 Agent 边"的落地 = 编排层分支（而非图结构嵌套）：
1. Entry 图每轮重跑（无 checkpointer，便宜）——多意图正确性：报修中插咨询
   （"顺便问密码"）被正确分流到咨询侧，不被报修会话吞掉
2. route == REPAIR → RepairGraph：get_state(cfg).next 非空（有 pending interrupt）
   → Command(resume=msg) 续跑；next 为空（新会话/已完成）→ 新 thread_id 全新会话
   （终态 thread 复用已实测产生 state 残留 + 重复中断，必须新 thread）
3. route == CONSULT → ConsultGraph（同款 invoke/resume；咨询侧 thread 独立，
   与报修会话互不干扰——多意图分流的关键）
4. M4 QualityAgent 惰性触发（需求 §6 触达方式已拍死）：user_id 非空时每轮先查
   "关闭超 24h 未回访"工单 → 有则先进 QualityGraph（提醒/采集），再进主流程
5. M5 投诉管道：route == COMPLAINT 且 complaint_graph 已注入 → 复用 RepairGraph
   （ticket_type="complaint"）建投诉单（同款 resume/invoke 判定）；
   complaint_graph 未注入 → 走占位回复（向后兼容旧调用方）
6. 其余（HUMAN_HANDOFF）→ 占位回复（转人工）

thread_id 由调用方管理（M6 前端会话 id；M3/M4 评测 runner 用 case id）。
Quality 用独立 thread（quality-{thread_id}）与主流程隔离。
"""

from langgraph.types import Command

from campus_desk import telemetry
from campus_desk.entry.routes import COMPLAINT, CONSULT, HUMAN_HANDOFF, REPAIR
from campus_desk.quality.pending import find_pending_reviews

# 非 REPAIR/CONSULT/COMPLAINT 路由的占位回复（COMPLAINT 已接投诉管道，
# 仅 HUMAN_HANDOFF 留占位；未注入 complaint_graph 时兜底用 entry 回复文案）
_NON_AGENT_REPLIES = {
    HUMAN_HANDOFF: "已为您转人工处理，稍后会有工作人员与您联系，请保持在线。",
}


def _quality_out(state: dict) -> dict:
    """Quality 轮输出（route=quality 标记，评测/调用方可识别）。"""
    return {
        "route": "quality",
        "reply": state.get("reply", ""),
        "pending_question": state.get("pending_question"),
        "outcome": state.get("outcome"),
        "finished": state.get("finished"),
    }


def turn(
    entry_graph,
    repair_graph,
    consult_graph,
    thread_id: str,
    msg: str,
    *,
    quality_graph=None,
    user_id: str | None = None,
    session_factory=None,
    complaint_graph=None,
) -> dict:
    """一轮对话（M5-T3 埋点 wrapper）：trace 属性 + orchestrator 根 span。

    trace 级属性：user_id → Langfuse user、thread_id → session（trace 归并到
    会话维度）；tags 标记 M5 埋点版本。无 key 时 trace_attrs/span 均 no-op。
    """
    with (
        telemetry.trace_attrs(user_id=user_id, session_id=thread_id, tags=["campusdesk-m5"]),
        telemetry.span("orchestrator.turn", metadata={"thread_id": thread_id}),
    ):
        return _turn_impl(
            entry_graph,
            repair_graph,
            consult_graph,
            thread_id,
            msg,
            quality_graph=quality_graph,
            user_id=user_id,
            session_factory=session_factory,
            complaint_graph=complaint_graph,
        )


def _turn_impl(
    entry_graph,
    repair_graph,
    consult_graph,
    thread_id: str,
    msg: str,
    *,
    quality_graph=None,
    user_id: str | None = None,
    session_factory=None,
    complaint_graph=None,
) -> dict:
    """一轮对话（实现体）：Quality 回访（可选）→ Entry 分流 → 按需进各 Agent 图。

    quality_graph + user_id + session_factory 全提供时才触发回访检查
    （评测/无身份场景缺省跳过；M4 起 QualityAgent 已实装）。
    complaint_graph（M5）：COMPLAINT 路由时复用 RepairGraph 建投诉单；
    未注入（None）时 COMPLAINT 走占位回复，兼容旧调用方。
    """
    # M4 QualityAgent 惰性触发：有待回访工单 → 先回访（提醒/采集），再进主流程
    if user_id and session_factory is not None and quality_graph is not None:
        quality_cfg = {"configurable": {"thread_id": f"quality-{thread_id}"}}
        if quality_graph.get_state(quality_cfg).next != ():
            # 回访进行中（等评分）→ 学生回答作为 resume 值采集
            with telemetry.span("agent.quality"):
                return _quality_out(quality_graph.invoke(Command(resume=msg), quality_cfg))
        pending = find_pending_reviews(session_factory, user_id)
        if pending:
            with telemetry.span("agent.quality"):
                state = quality_graph.invoke(
                    {"user_input": msg, "pending_tickets": pending}, quality_cfg
                )
                return _quality_out(state)

    entry_out = entry_graph.invoke({"user_input": msg})
    route = entry_out["route"]
    cfg = {"configurable": {"thread_id": thread_id}}
    repair_pending = repair_graph.get_state(cfg).next != ()
    consult_pending = consult_graph.get_state(cfg).next != ()
    complaint_pending = complaint_graph is not None and complaint_graph.get_state(cfg).next != ()

    if route == REPAIR:
        with telemetry.span("agent.repair", metadata={"thread_id": thread_id}):
            if repair_pending:
                # 有挂起的报修会话（等在 wait 节点）→ 学生回复作为 resume 值续跑。
                # 含"other 类补充信息"：挂起中学生的回答（"3号楼501，李华"）被 Entry
                # 无上下文地判为 other→HUMAN_HANDOFF——但这是对追问的回答不是新话题，
                # 仍进报修流程（真 LLM 评测抓出：修复前这类回复走人工占位，永不 resume）
                state = repair_graph.invoke(Command(resume=msg), cfg)
            else:
                # 无挂起 → 新报修会话（thread_id 语义 = 报修会话 id，调用方保证唯一）
                state = repair_graph.invoke({"user_input": msg}, cfg)
            return {
                "reply": state.get("reply", ""),
                "route": REPAIR,
                "pending_question": state.get("pending_question"),
                "ticket_id": state.get("ticket_id"),
                "ticket_status": state.get("ticket_status"),
                "finished": state.get("finished"),
                "tool_calls": state.get("tool_calls", []),
                "status_events": state.get("status_events", []),
            }

    if route == CONSULT:
        with telemetry.span("agent.consult", metadata={"thread_id": thread_id}):
            if consult_pending:
                state = consult_graph.invoke(Command(resume=msg), cfg)
            else:
                state = consult_graph.invoke({"user_input": msg}, cfg)
            return {
                "reply": state.get("reply", ""),
                "route": CONSULT,
                "pending_question": state.get("pending_question"),
                "finished": state.get("finished"),
                "outcome": state.get("outcome"),
                "handoff_package": state.get("handoff_package"),
                "tool_calls": state.get("tool_calls", []),
            }

    if route == COMPLAINT:
        if complaint_graph is None:
            # 未注入投诉管道（旧调用方）→ 走占位回复（文案来自 entry 图）
            return {
                "reply": entry_out.get("reply", ""),
                "route": COMPLAINT,
                "secondary_intents": entry_out.get("intent", None)
                and entry_out["intent"].secondary_intents
                or [],
            }
        # 镜像 REPAIR 分支：有挂起的投诉会话（等联系人追问）→ resume 续跑；
        # 无挂起 → 新投诉会话（thread_id 语义 = 投诉会话 id，与报修会话隔离）
        with telemetry.span("agent.complaint", metadata={"thread_id": thread_id}):
            if complaint_pending:
                state = complaint_graph.invoke(Command(resume=msg), cfg)
            else:
                state = complaint_graph.invoke({"user_input": msg}, cfg)
        return {
            "reply": state.get("reply", ""),
            "route": COMPLAINT,
            "pending_question": state.get("pending_question"),
            "ticket_id": state.get("ticket_id"),
            "ticket_status": state.get("ticket_status"),
            "finished": state.get("finished"),
            "tool_calls": state.get("tool_calls", []),
            "status_events": state.get("status_events", []),
        }

    # 报修挂起中补信息被判 HUMAN_HANDOFF → resume 进 RepairGraph（M3 坑，见上）
    if repair_pending and route == HUMAN_HANDOFF:
        with telemetry.span("agent.repair", metadata={"thread_id": thread_id}):
            state = repair_graph.invoke(Command(resume=msg), cfg)
            return {
                "reply": state.get("reply", ""),
                "route": REPAIR,
                "pending_question": state.get("pending_question"),
                "ticket_id": state.get("ticket_id"),
                "ticket_status": state.get("ticket_status"),
                "finished": state.get("finished"),
                "tool_calls": state.get("tool_calls", []),
                "status_events": state.get("status_events", []),
            }

    # 咨询挂起中补信息被判 HUMAN_HANDOFF → resume 进 ConsultGraph（M4 同源坑：
    # 学生回答"3号楼/学号2024001"被 Entry 无上下文判 other → 落占位，
    # ConsultGraph 永不 resume，真 LLM 评测 outcome=None 抓出）
    if consult_pending and route == HUMAN_HANDOFF:
        with telemetry.span("agent.consult", metadata={"thread_id": thread_id}):
            state = consult_graph.invoke(Command(resume=msg), cfg)
            return {
                "reply": state.get("reply", ""),
                "route": CONSULT,
                "pending_question": state.get("pending_question"),
                "finished": state.get("finished"),
                "outcome": state.get("outcome"),
                "handoff_package": state.get("handoff_package"),
                "tool_calls": state.get("tool_calls", []),
            }

    # 投诉挂起中补信息被判 HUMAN_HANDOFF → resume 进 complaint_graph
    # （M5 同源坑：学生回答"李华"被 Entry 无上下文判 other → 落占位永不 resume）
    if complaint_pending and route == HUMAN_HANDOFF:
        with telemetry.span("agent.complaint", metadata={"thread_id": thread_id}):
            state = complaint_graph.invoke(Command(resume=msg), cfg)
        return {
            "reply": state.get("reply", ""),
            "route": COMPLAINT,
            "pending_question": state.get("pending_question"),
            "ticket_id": state.get("ticket_id"),
            "ticket_status": state.get("ticket_status"),
            "finished": state.get("finished"),
            "tool_calls": state.get("tool_calls", []),
            "status_events": state.get("status_events", []),
        }

    return {
        "reply": _NON_AGENT_REPLIES.get(route, entry_out.get("reply", "")),
        "route": route,
        "secondary_intents": entry_out.get("intent", None)
        and entry_out["intent"].secondary_intents
        or [],
    }
