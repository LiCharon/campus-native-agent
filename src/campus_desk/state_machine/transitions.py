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

        _, target = EVENT_TRANSITIONS[event]  # sources 校验由 validate_transition 完成
        validate_transition(ticket.status, target, event, actor)

        from_status: TicketStatus = ticket.status
        ticket.status = target
        if target == "CLOSED":
            # 关闭时间（M4 QualityAgent 回访 24h 判定用；关闭 = 字段不是状态）
            ticket.closed_at = datetime.now(UTC)
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


def apply_escalation(session, ticket_id: int, actor: str, note: str = "") -> None:
    """超时升级 = 字段不是状态（M5 扫描器用）：escalation_count + 1 + escalated_at + 审计日志。

    与 apply_transition 相同的事务骨架（SAVEPOINT + 行锁，可嵌套进调用方外层事务）；
    工单留在原状态（不跳转），故 TicketLog 的 from_status == to_status == 当前状态。
    CLOSED/CANCELLED 终态工单抛 ValueError（不可升级）。
    """
    with session.begin_nested():
        ticket = session.execute(
            select(Ticket).where(Ticket.id == ticket_id).with_for_update()
        ).scalar_one_or_none()
        if ticket is None:
            raise TicketNotFound(ticket_id)
        if ticket.status in ("CLOSED", "CANCELLED"):
            raise ValueError(f"终态工单不可升级: #{ticket_id}（status={ticket.status}）")

        current_status: TicketStatus = ticket.status
        ticket.escalation_count += 1
        ticket.escalated_at = datetime.now(UTC)

        session.add(
            TicketLog(
                ticket_id=ticket_id,
                from_status=current_status,
                to_status=current_status,
                actor=actor,
                note=f"超时升级（第 {ticket.escalation_count} 次）: {note}",
            )
        )
