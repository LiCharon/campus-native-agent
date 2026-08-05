"""每轮编排（M3 Repair + M4 Consult）：Entry 分流 → REPAIR 进 RepairGraph / CONSULT 进 ConsultGraph。

"入口路由处挂下游 Agent 边"的落地 = 编排层分支（而非图结构嵌套）：
1. Entry 图每轮重跑（无 checkpointer，便宜）——多意图正确性：报修中插咨询
   （"顺便问密码"）被正确分流到咨询侧，不被报修会话吞掉
2. route == REPAIR → RepairGraph：get_state(cfg).next 非空（有 pending interrupt）
   → Command(resume=msg) 续跑；next 为空（新会话/已完成）→ 新 thread_id 全新会话
   （终态 thread 复用已实测产生 state 残留 + 重复中断，必须新 thread）
3. route == CONSULT → ConsultGraph（同款 invoke/resume；咨询侧 thread 独立，
   与报修会话互不干扰——多意图分流的关键）
4. 其余（COMPLAINT/HUMAN_HANDOFF）→ 占位回复（M5 接投诉管道/转人工）

thread_id 由调用方管理（M6 前端会话 id；M3/M4 评测 runner 用 case id）。
"""

from langgraph.types import Command

from campus_desk.entry.routes import COMPLAINT, CONSULT, HUMAN_HANDOFF, REPAIR

# 非 REPAIR/CONSULT 路由的占位回复（M5 接投诉管道后替换）
_NON_AGENT_REPLIES = {
    COMPLAINT: "收到您的投诉，已为您升级处理，稍后有专人跟进。",
    HUMAN_HANDOFF: "已为您转人工处理，稍后会有工作人员与您联系，请保持在线。",
}


def turn(entry_graph, repair_graph, consult_graph, thread_id: str, msg: str) -> dict:
    """一轮对话：Entry 分流 → 按需进 Repair/ConsultGraph。返回给调用方（reply/route/state 摘要）。

    consult_graph 必传（M4 起 CONSULT 路由已实装；测试传 fake 图或真图）。
    """
    entry_out = entry_graph.invoke({"user_input": msg})
    route = entry_out["route"]
    cfg = {"configurable": {"thread_id": thread_id}}
    repair_pending = repair_graph.get_state(cfg).next != ()
    consult_pending = consult_graph.get_state(cfg).next != ()

    if route == REPAIR:
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

    # 报修挂起中补信息被判 HUMAN_HANDOFF → resume 进 RepairGraph（M3 坑，见上）
    if repair_pending and route == HUMAN_HANDOFF:
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

    return {
        "reply": _NON_AGENT_REPLIES.get(route, entry_out.get("reply", "")),
        "route": route,
        "secondary_intents": entry_out.get("intent", None)
        and entry_out["intent"].secondary_intents
        or [],
    }
