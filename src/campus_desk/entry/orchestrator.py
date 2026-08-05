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
5. 其余（COMPLAINT/HUMAN_HANDOFF）→ 占位回复（M5 接投诉管道/转人工）

thread_id 由调用方管理（M6 前端会话 id；M3/M4 评测 runner 用 case id）。
Quality 用独立 thread（quality-{thread_id}）与主流程隔离。
"""

from langgraph.types import Command

from campus_desk.entry.routes import COMPLAINT, CONSULT, HUMAN_HANDOFF, REPAIR
from campus_desk.quality.pending import find_pending_reviews

# 非 REPAIR/CONSULT 路由的占位回复（M5 接投诉管道后替换）
_NON_AGENT_REPLIES = {
    COMPLAINT: "收到您的投诉，已为您升级处理，稍后有专人跟进。",
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
) -> dict:
    """一轮对话：Quality 回访（可选）→ Entry 分流 → 按需进 Repair/ConsultGraph。

    quality_graph + user_id + session_factory 全提供时才触发回访检查
    （评测/无身份场景缺省跳过；M4 起 QualityAgent 已实装）。
    """
    # M4 QualityAgent 惰性触发：有待回访工单 → 先回访（提醒/采集），再进主流程
    if user_id and session_factory is not None and quality_graph is not None:
        quality_cfg = {"configurable": {"thread_id": f"quality-{thread_id}"}}
        if quality_graph.get_state(quality_cfg).next != ():
            # 回访进行中（等评分）→ 学生回答作为 resume 值采集
            return _quality_out(quality_graph.invoke(Command(resume=msg), quality_cfg))
        pending = find_pending_reviews(session_factory, user_id)
        if pending:
            state = quality_graph.invoke(
                {"user_input": msg, "pending_tickets": pending}, quality_cfg
            )
            return _quality_out(state)

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

    # 咨询挂起中补信息被判 HUMAN_HANDOFF → resume 进 ConsultGraph（M4 同源坑：
    # 学生回答"3号楼/学号2024001"被 Entry 无上下文判 other → 落占位，
    # ConsultGraph 永不 resume，真 LLM 评测 outcome=None 抓出）
    if consult_pending and route == HUMAN_HANDOFF:
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

    return {
        "reply": _NON_AGENT_REPLIES.get(route, entry_out.get("reply", "")),
        "route": route,
        "secondary_intents": entry_out.get("intent", None)
        and entry_out["intent"].secondary_intents
        or [],
    }
