"""每轮编排（M3）：Entry 分流 → REPAIR 时进 RepairGraph。

"入口路由 REPAIR 处挂 RepairAgent 边"的落地 = 编排层分支（而非图结构嵌套）：
1. Entry 图每轮重跑（无 checkpointer，便宜）——多意图正确性：报修中插咨询
   （"顺便问密码"）被正确分流到咨询侧，不被报修会话吞掉
2. route == REPAIR → RepairGraph：
   - get_state(cfg).next 非空（有 pending interrupt）→ Command(resume=msg) 续跑
   - next 为空（新会话/已完成）→ 新 thread_id 全新会话（终态 thread 复用
     已实测产生 state 残留 + 重复中断，必须新 thread）
3. 非 REPAIR → M2 风格占位回复（M4 接 ConsultAgent/投诉管道）

thread_id 由调用方管理（M6 前端会话 id；M3 评测 runner 用 case id）。
"""

from langgraph.types import Command

from campus_desk.entry.routes import COMPLAINT, CONSULT, HUMAN_HANDOFF, REPAIR

# 非 REPAIR 路由的占位回复（M4 接 ConsultAgent 后替换）
_NON_REPAIR_REPLIES = {
    CONSULT: "好的，我来帮您处理咨询问题，请稍等。",
    COMPLAINT: "收到您的投诉，已为您升级处理，稍后有专人跟进。",
    HUMAN_HANDOFF: "已为您转人工处理，稍后会有工作人员与您联系，请保持在线。",
}


def turn(entry_graph, repair_graph, thread_id: str, msg: str) -> dict:
    """一轮对话：Entry 分流 → 按需进 RepairGraph。返回给调用方（reply/route/state 摘要）。"""
    entry_out = entry_graph.invoke({"user_input": msg})
    route = entry_out["route"]

    if route != REPAIR:
        return {
            "reply": _NON_REPAIR_REPLIES.get(route, entry_out.get("reply", "")),
            "route": route,
            "secondary_intents": entry_out.get("intent", None)
            and entry_out["intent"].secondary_intents
            or [],
        }

    cfg = {"configurable": {"thread_id": thread_id}}
    if repair_graph.get_state(cfg).next:
        # 有挂起的报修会话（等在 wait 节点）→ 学生回复作为 resume 值续跑
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
