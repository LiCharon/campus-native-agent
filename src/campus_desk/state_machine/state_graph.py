"""工单状态机图（M3）：6 节点白名嘴边——白名单的图渲染。

叙事：状态跳转 = LangGraph 图的边 = 天然白名单（需求 §3），非法跳转在图结构
上不存在。DB 状态是唯一事实源（apply_transition 写库），此图节点只把
(ticket_id, status) 记入 state 的 status_events（评测/审计可读），不重复写 DB。

图结构：每状态节点的出边 = 条件边（映射表只含白名单目标）——PENDING_VERIFY
双出边（CLOSED/IN_PROGRESS）是天然分支，必须用条件边，普通边会并行冲突
（LangGraph "Can receive only one value per step"）。路径选择：state["_next"]
显式指定（非法目标被映射表挡住），缺省走排序第一的目标（完整链路路径）。

RepairGraph 不嵌套此图（状态变更直接调 apply_transition）；此图独立存在：
测试从编译图反查白名嘴边锁定 + 面试展示。
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from campus_desk.state_machine.machine import ALL_STATUSES, VALID_TRANSITIONS, TicketStatus

# 缺省下一跳（无 _next 时）：闭环主路径——ASSIGNED 分支不能走字母排序
# （sorted 会让 ASSIGNED 优先到 CANCELLED）
_DEFAULT_NEXT: dict[str, str] = {
    "SUBMITTED": "ASSIGNED",
    "ASSIGNED": "IN_PROGRESS",
    "IN_PROGRESS": "PENDING_VERIFY",
    "PENDING_VERIFY": "CLOSED",
}


class TicketGraphState(TypedDict):
    ticket_id: int
    status: TicketStatus
    status_events: list[str]  # "SUBMITTED->ASSIGNED" 跳转记录（测试/评测断言）
    _next: TicketStatus  # 显式指定下一跳（条件边路由；缺省走默认路径）


def _make_arrive(status: TicketStatus):
    def arrive(state: TicketGraphState) -> dict:
        if state.get("status") != status:
            events = list(state.get("status_events", []))
            events.append(f"{state.get('status')}->{status}")
            return {"status": status, "status_events": events}
        return {}

    return arrive


def _make_router(src: TicketStatus):
    """条件边路由：_next 在白名单内走之，否则走排序第一目标（默认路径）。"""

    def route(state: TicketGraphState) -> str:
        next_target = state.get("_next")
        if next_target in VALID_TRANSITIONS[src]:
            return next_target
        return _DEFAULT_NEXT[src]

    return route


def build_ticket_state_graph():
    """6 节点白名嘴边状态机图（条件边 = 唯一出边，映射表 = 白名单）。"""
    graph = StateGraph(TicketGraphState)
    for status in ALL_STATUSES:
        graph.add_node(status, _make_arrive(status))

    graph.add_edge(START, "SUBMITTED")  # 新工单落库即为 SUBMITTED
    for src in ALL_STATUSES:
        targets = VALID_TRANSITIONS[src]
        if targets:
            graph.add_conditional_edges(src, _make_router(src), {t: t for t in targets})
    graph.add_edge("CLOSED", END)
    graph.add_edge("CANCELLED", END)
    return graph.compile()
