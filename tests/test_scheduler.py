"""超时升级扫描测试（M5-T1）：scan_overdue 阈值/防抖/时钟注入 + 后台调度器注册。

核心断言：
- P1 超 4h / P2 超 48h 升级一次（escalation 字段，状态不变，actor=system）
- escalation_count==0 过滤防抖：只升一次
- P3 跳过；PENDING_VERIFY 挂起 72h 自动关闭
- now 注入决定性：同一批数据两个 now 不同结果；同 now 幂等

⚠️ 测试模式与 test_transitions.py 一致（SQLAlchemy 2.0 显式事务语义）：
1. 所有读写包进显式 with session.begin() 块——SELECT 会隐式开启事务，
   与 begin()/begin_nested() 混用报 "A transaction is already begun"
2. 造数 helper 返回整数 id 而非 ORM 实例——session.rollback() 会 expire 实例，
   之后访问实例属性触发惰性加载并 autobegin，与显式 begin() 冲突
3. 造数时 created_at/updated_at 手动指定（updated_at 有 onupdate，INSERT 不受影响）；
   时间比较全部发生在 SQL 层（SQLAlchemy 渲染一致），Python 层只断言状态/计数/日志
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from campus_desk.db.models import Ticket, TicketLog
from campus_desk.scheduler.escalation import scan_overdue, start_scheduler

# 固定基准时钟：所有造数与扫描注入共用，保证用例可复现
NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)


def _create_ticket(
    session,
    *,
    status: str = "SUBMITTED",
    priority: str = "P2",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    escalation_count: int = 0,
) -> int:
    """直写造数并立即返回整数 id（created_at/updated_at 手动指定覆盖 default/onupdate）。"""
    with session.begin():
        t = Ticket(
            user_id="student-001",
            description="测试工单",
            contact="李华",
            status=status,
            priority=priority,
            escalation_count=escalation_count,
        )
        if created_at is not None:
            t.created_at = created_at
        if updated_at is not None:
            t.updated_at = updated_at
        session.add(t)
        session.flush()
        ticket_id = t.id
    return ticket_id


def _scan(session_factory, **kwargs):
    return scan_overdue(session_factory, **kwargs)


def _get(session, ticket_id: int) -> Ticket:
    with session.begin():
        return session.get(Ticket, ticket_id)


def _logs(session, ticket_id: int) -> list[TicketLog]:
    with session.begin():
        return list(
            session.execute(select(TicketLog).where(TicketLog.ticket_id == ticket_id)).scalars()
        )


class TestEscalation:
    def test_p1_overdue_escalates(self, db_session_factory):
        """P1 超 5h：升级计数 +1、escalated_at 非空、状态不变、日志 from==to、actor=system。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(
                session, status="SUBMITTED", priority="P1", created_at=NOW - timedelta(hours=5)
            )
            result = _scan(db_session_factory, now=NOW)
            assert result.escalated == [ticket_id]
            got = _get(session, ticket_id)
            assert got.escalation_count == 1
            assert got.escalated_at is not None
            assert got.status == "SUBMITTED"  # 升级 = 字段不是状态
            logs = _logs(session, ticket_id)
            assert len(logs) == 1
            assert logs[0].from_status == "SUBMITTED"
            assert logs[0].to_status == "SUBMITTED"
            assert logs[0].actor == "system"
            assert logs[0].note == "超时升级（第 1 次）: 超时未处理"

    def test_p2_overdue_and_not(self, db_session_factory):
        """P2 超 49h 升 / P2 超 10h 不升（同一 now 注入下，48h 阈值精确生效）。"""
        with db_session_factory() as session:
            overdue = _create_ticket(session, priority="P2", created_at=NOW - timedelta(hours=49))
            fresh = _create_ticket(session, priority="P2", created_at=NOW - timedelta(hours=10))
            result = _scan(db_session_factory, now=NOW)
            assert result.escalated == [overdue]
            assert _get(session, overdue).escalation_count == 1
            assert _get(session, fresh).escalation_count == 0

    def test_escalate_once_only(self, db_session_factory):
        """防抖：escalation_count==1 后即使更晚再扫也不再升（只升一次）。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session, priority="P1", created_at=NOW - timedelta(hours=5))
            first = _scan(db_session_factory, now=NOW)
            assert first.escalated == [ticket_id]
            second = _scan(db_session_factory, now=NOW + timedelta(hours=10))
            assert second.escalated == []
            got = _get(session, ticket_id)
            assert got.escalation_count == 1
            assert len(_logs(session, ticket_id)) == 1  # 日志也只写一条

    def test_p3_skipped(self, db_session_factory):
        """P3 预约单超时跳过（预约不适用超时升级）。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(
                session, priority="P3", created_at=NOW - timedelta(hours=100)
            )
            result = _scan(db_session_factory, now=NOW)
            assert result.escalated == []
            assert _get(session, ticket_id).escalation_count == 0

    def test_closed_and_cancelled_never_escalated(self, db_session_factory):
        """终态工单不进升级候选（CLOSED/CANCELLED 不在活动态集合），直接升级抛 ValueError。"""
        from campus_desk.state_machine.transitions import apply_escalation

        with db_session_factory() as session:
            closed = _create_ticket(
                session, status="CLOSED", priority="P1", created_at=NOW - timedelta(hours=5)
            )
            cancelled = _create_ticket(
                session, status="CANCELLED", priority="P1", created_at=NOW - timedelta(hours=5)
            )
            result = _scan(db_session_factory, now=NOW)
            assert result.escalated == []
            assert _get(session, closed).escalation_count == 0
            assert _get(session, cancelled).escalation_count == 0
            # 升级写入层防御：终态直接调用抛 ValueError，SAVEPOINT 回滚零残留
            for tid in (closed, cancelled):
                with pytest.raises(ValueError), session.begin():
                    apply_escalation(session, tid, "system")
                assert _get(session, tid).escalation_count == 0


class TestAutoClose:
    def test_pending_verify_auto_close(self, db_session_factory):
        """PENDING_VERIFY 挂起 73h 自动关闭（updated_at 起算）；3h 前不动。"""
        with db_session_factory() as session:
            stale = _create_ticket(
                session, status="PENDING_VERIFY", updated_at=NOW - timedelta(hours=73)
            )
            fresh = _create_ticket(
                session, status="PENDING_VERIFY", updated_at=NOW - timedelta(hours=3)
            )
            result = _scan(db_session_factory, now=NOW)
            assert result.auto_closed == [stale]
            assert _get(session, stale).status == "CLOSED"
            assert _get(session, fresh).status == "PENDING_VERIFY"
            logs = _logs(session, stale)
            assert logs[-1].note == "超时自动关闭"
            assert logs[-1].actor == "system"


class TestClockDeterminism:
    def test_different_now_different_result(self, db_session_factory):
        """时钟注入决定性：同一批数据，now 不同 → 结果不同。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session, priority="P1", created_at=NOW - timedelta(hours=5))
            early = _scan(db_session_factory, now=NOW - timedelta(hours=10))  # 尚未超时
            assert early.escalated == []
            late = _scan(db_session_factory, now=NOW)  # 已超 5h
            assert late.escalated == [ticket_id]

    def test_same_now_twice_idempotent(self, db_session_factory):
        """幂等：同 now 跑两次，第二次零处置（升级/关闭都只做一次）。"""
        with db_session_factory() as session:
            esc = _create_ticket(session, priority="P1", created_at=NOW - timedelta(hours=5))
            close = _create_ticket(
                session, status="PENDING_VERIFY", updated_at=NOW - timedelta(hours=73)
            )
            first = _scan(db_session_factory, now=NOW)
            assert first.escalated == [esc]
            assert first.auto_closed == [close]
            second = _scan(db_session_factory, now=NOW)
            assert second.escalated == []
            assert second.auto_closed == []
            assert _get(session, esc).escalation_count == 1
            assert _get(session, close).status == "CLOSED"

    def test_scan_result_fields(self, db_session_factory):
        """ScanResult 字段正确：一次扫描同时处置升级与自动关闭两类工单。"""
        with db_session_factory() as session:
            esc = _create_ticket(session, priority="P1", created_at=NOW - timedelta(hours=5))
            close = _create_ticket(
                session, status="PENDING_VERIFY", updated_at=NOW - timedelta(hours=73)
            )
            untouched = _create_ticket(session, priority="P2", created_at=NOW - timedelta(hours=10))
            result = _scan(db_session_factory, now=NOW)
            assert result.escalated == [esc]
            assert result.auto_closed == [close]
            assert untouched not in result.escalated
            assert _get(session, untouched).escalation_count == 0


class TestScheduler:
    def test_start_scheduler_registers_interval_job(self, db_session_factory):
        """start_scheduler 返回已启动的调度器：注册 1 个 interval job，参数正确。"""
        sched = start_scheduler(db_session_factory, interval_seconds=30)
        try:
            jobs = sched.get_jobs()
            assert len(jobs) == 1
            job = jobs[0]
            assert job.trigger.interval.total_seconds() == 30
            assert job.max_instances == 1
            assert job.coalesce is True
            assert job.misfire_grace_time == 30
        finally:
            sched.shutdown(wait=False)  # 不阻塞测试
