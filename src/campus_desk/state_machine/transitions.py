"""状态变更的唯一写入口（M3）：apply_transition——状态 + 审计日志原子落库。

事务边界：begin_nested()（SAVEPOINT）——可嵌套进调用方的外层事务
（工具短会话 with session.begin() / RepairGraph 的 create+assign 一步事务），
校验失败只回滚本操作，不影响外层；无外层事务时由调用方负责最终提交。
行锁：with_for_update()（MySQL 生效防并发派单，SQLite 为 no-op——M3 不测并发，注明）。

超时升级 = 字段不是状态（需求 §3）：escalate=True 时 escalation_count + 1、
escalated_at 置当前时间，工单留在原状态继续流转。
"""

from datetime import UTC, datetime

from sqlalchemy import select

from campus_desk.db.models import Ticket, TicketLog
from campus_desk.state_machine.machine import (
    EVENT_TRANSITIONS,
    TicketEvent,
    TicketStatus,
    TransitionRecord,
    validate_transition,
)


class TicketNotFound(Exception):
    def __init__(self, ticket_id: int):
        self.ticket_id = ticket_id
        super().__init__(f"工单不存在: #{ticket_id}")


def apply_transition(
    session,
    ticket_id: int,
    event: TicketEvent,
    actor: str,
    note: str = "",
    *,
    repairman_id: str | None = None,
    dept: str | None = None,
    escalate: bool = False,
) -> TransitionRecord:
    """执行一次状态跳转（SAVEPOINT 原子）。返回跳转记录；非法跳转抛 TransitionError。

    assign 事件可带 repairman_id/dept（派单落库）；escalate 触发超时升级字段。
    """
    with session.begin_nested():
        ticket = session.execute(
            select(Ticket).where(Ticket.id == ticket_id).with_for_update()
        ).scalar_one_or_none()
        if ticket is None:
            raise TicketNotFound(ticket_id)

        sources, target = EVENT_TRANSITIONS[event]
        validate_transition(ticket.status, target, event, actor)

        from_status: TicketStatus = ticket.status
        ticket.status = target
        if event == "assign":
            if repairman_id is not None:
                ticket.repairman_id = repairman_id
            if dept is not None:
                ticket.dept = dept
        if escalate:
            ticket.escalation_count += 1
            ticket.escalated_at = datetime.now(UTC)

        session.add(
            TicketLog(
                ticket_id=ticket_id,
                from_status=from_status,
                to_status=target,
                actor=actor,
                note=note,
            )
        )

    return TransitionRecord(
        ticket_id=ticket_id,
        from_status=from_status,
        to_status=target,
        event=event,
        actor=actor,
    )
