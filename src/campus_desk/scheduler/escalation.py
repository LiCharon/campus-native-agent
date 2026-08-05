"""定时超时扫描（M5-T1）：升级超时工单 + 自动关闭挂起工单（需求 §3）。

纯确定性逻辑：now 由调用方注入（缺省 datetime.now(UTC)），所有阈值 cutoff
都用注入的 now 推算，查询内不调用 datetime.now()——时钟注入决定性是可测核心。

PENDING_VERIFY 的 72h 起算点用 updated_at 近似"进入该状态的时间"：工单停在
PENDING_VERIFY 时 updated_at == 最后一次状态跳转时间（进入挂起的时刻，onupdate
自动刷新）；若返工重开则 updated_at 顺延，起算点随之重置——语义合理。

升级只做一次（escalation_count==0 过滤，防抖）；升级 = 字段不是状态
（escalation_count/escalated_at，见 transitions.apply_escalation），工单留原状态。

APScheduler 必须 <4（v4 为异步重构，BackgroundScheduler 已不存在），
pyproject.toml 依赖已锁定 "apscheduler>=3.11,<4"。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from campus_desk.config import settings
from campus_desk.db.models import Ticket
from campus_desk.state_machine.transitions import apply_escalation, apply_transition

_ESCALABLE_STATUSES = ("SUBMITTED", "ASSIGNED", "IN_PROGRESS")


@dataclass
class ScanResult:
    """一次扫描的处置清单（升级工单 id / 自动关闭工单 id，按 id 升序）。"""

    escalated: list[int]
    auto_closed: list[int]


def scan_overdue(
    session_factory,
    *,
    now: datetime | None = None,
    p1_hours: int | None = None,
    p2_hours: int | None = None,
    auto_close_hours: int | None = None,
) -> ScanResult:
    """扫描一次超时工单：P1/P2 超阈值升级一次 + PENDING_VERIFY 挂起自动关闭。

    阈值缺省取 settings（escalation_p1_hours / escalation_p2_hours /
    auto_close_hours）；now 缺省取当前 UTC 时间。同一会话内完成全部处置：
    apply_escalation / apply_transition 的 SAVEPOINT 嵌套进外层 begin()，
    任一步失败整体回滚（全有或全无）。
    """
    now = now if now is not None else datetime.now(UTC)
    p1_hours = p1_hours if p1_hours is not None else settings.escalation_p1_hours
    p2_hours = p2_hours if p2_hours is not None else settings.escalation_p2_hours
    auto_close_hours = (
        auto_close_hours if auto_close_hours is not None else settings.auto_close_hours
    )

    result = ScanResult(escalated=[], auto_closed=[])

    with session_factory() as session, session.begin():
        # 升级候选：活动态 + 未升级过（escalation_count==0 防抖，只升一次）+ P1/P2 超各自阈值
        cutoff_p1 = now - timedelta(hours=p1_hours)
        cutoff_p2 = now - timedelta(hours=p2_hours)
        escalate_ids = session.execute(
            select(Ticket.id)
            .where(
                Ticket.status.in_(_ESCALABLE_STATUSES),
                Ticket.escalation_count == 0,
                Ticket.priority.in_(("P1", "P2")),
                ((Ticket.priority == "P1") & (Ticket.created_at < cutoff_p1))
                | ((Ticket.priority == "P2") & (Ticket.created_at < cutoff_p2)),
            )
            .order_by(Ticket.id)
        ).scalars()
        for ticket_id in escalate_ids:
            apply_escalation(session, ticket_id, "system", note="超时未处理")
            result.escalated.append(ticket_id)

        # 挂起自动关闭：PENDING_VERIFY 超 auto_close_hours（updated_at 近似进入该状态时间）
        cutoff_close = now - timedelta(hours=auto_close_hours)
        close_ids = session.execute(
            select(Ticket.id)
            .where(
                Ticket.status == "PENDING_VERIFY",
                Ticket.updated_at < cutoff_close,
            )
            .order_by(Ticket.id)
        ).scalars()
        for ticket_id in close_ids:
            apply_transition(session, ticket_id, "auto_close", "system", note="超时自动关闭")
            result.auto_closed.append(ticket_id)

    return result


def start_scheduler(session_factory, *, interval_seconds: int | None = None):
    """启动后台调度器：每 interval_seconds（缺省 settings.scan_interval_seconds）扫一次。

    max_instances=1 + coalesce=True + misfire_grace_time=30：上次未跑完则跳过、
    堆积的错失运行折叠为最后一次、迟到超 30s 的运行不执行——防重复扫描。
    返回已 start 的 BackgroundScheduler，调用方负责在进程退出前 shutdown()。
    """
    sched = BackgroundScheduler()
    sched.add_job(
        scan_overdue,
        "interval",
        args=[session_factory],
        seconds=(
            interval_seconds if interval_seconds is not None else settings.scan_interval_seconds
        ),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    sched.start()
    return sched
