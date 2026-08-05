"""状态机事务应用测试（M3）：apply_transition 的原子性/审计/升级字段。

核心断言：
- 合法跳转：状态 + 审计日志同事务落库
- 非法跳转：抛错且零残留（状态不变、无日志）——整体回滚
- 升级 = 字段：escalate 递增计数 + 置 escalated_at，工单留在原状态
- auto_close：备注"超时自动关闭"

⚠️ 测试模式（SQLAlchemy 2.0 显式事务语义）：
1. 所有读写包进显式 with session.begin() 块——SELECT 会隐式开启事务，
   与 begin()/begin_nested() 混用报 "A transaction is already begun"
2. 建单 helper 返回整数 id 而非 ORM 实例——session.rollback()（失败路径）
   会 expire 所有实例，之后访问实例属性触发惰性加载并 autobegin 事务，
   与后续显式 begin() 冲突（本项目排查实录，记入 DEV_JOURNAL）
"""

import pytest
from sqlalchemy import select

from campus_desk.db.models import Ticket, TicketLog
from campus_desk.state_machine.machine import TransitionError
from campus_desk.state_machine.transitions import TicketNotFound, apply_transition


def _create_ticket(session, status: str = "SUBMITTED") -> int:
    """建单并立即返回整数 id（status 可预设中间态，测终态/特定源状态用）。"""
    with session.begin():
        t = Ticket(user_id="student-001", description="灯坏了", contact="李华")
        session.add(t)
        session.flush()
        if status != "SUBMITTED":
            t.status = status
        ticket_id = t.id  # 事务内取出，之后只传整数 id
    return ticket_id


def _apply(session, ticket_id: int, event: str, actor: str, **kwargs):
    """显式 begin 包住 apply_transition：SAVEPOINT 释放后提交落库。"""
    with session.begin():
        return apply_transition(session, ticket_id, event, actor, **kwargs)


def _get(session, ticket_id: int) -> Ticket:
    with session.begin():
        return session.get(Ticket, ticket_id)


def _logs(session, ticket_id: int) -> list[TicketLog]:
    with session.begin():
        return list(
            session.execute(select(TicketLog).where(TicketLog.ticket_id == ticket_id)).scalars()
        )


class TestLegalTransitions:
    def test_assign_creates_log(self, db_session_factory):
        """SUBMITTED→ASSIGNED：状态 + 日志同事务（含派单信息）。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session)
            record = _apply(
                session,
                ticket_id,
                "assign",
                "admin-001",
                note="派给陈师傅",
                repairman_id="rm-001",
                dept="后勤",
            )
            assert record["from_status"] == "SUBMITTED"
            assert record["to_status"] == "ASSIGNED"
            got = _get(session, ticket_id)
            assert got.status == "ASSIGNED"
            assert got.repairman_id == "rm-001"
            assert got.dept == "后勤"
            logs = _logs(session, ticket_id)
            assert len(logs) == 1
            assert logs[0].from_status == "SUBMITTED"
            assert logs[0].to_status == "ASSIGNED"
            assert logs[0].actor == "admin-001"
            assert logs[0].note == "派给陈师傅"

    def test_cancel_from_both_sources(self, db_session_factory):
        """cancel 双源：SUBMITTED 和 ASSIGNED 都可撤。"""
        with db_session_factory() as session:
            t1 = _create_ticket(session)
            _apply(session, t1, "cancel", "student-001", note="改主意了")
            assert _get(session, t1).status == "CANCELLED"

            t2 = _create_ticket(session, status="ASSIGNED")
            _apply(session, t2, "cancel", "student-001")
            assert _get(session, t2).status == "CANCELLED"

    def test_full_chain_to_closed(self, db_session_factory):
        """完整链路：assign → start → complete → verify_ok → CLOSED。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session)
            for ev in ["assign", "start", "complete", "verify_ok"]:
                _apply(session, ticket_id, ev, actor="system")
            assert _get(session, ticket_id).status == "CLOSED"
            assert len(_logs(session, ticket_id)) == 4

    def test_rework_returns_to_in_progress(self, db_session_factory):
        """验收不通过返工：PENDING_VERIFY→IN_PROGRESS。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session, status="PENDING_VERIFY")
            record = _apply(session, ticket_id, "rework", "student-001", note="没修好")
            assert record["to_status"] == "IN_PROGRESS"
            assert _get(session, ticket_id).status == "IN_PROGRESS"

    def test_auto_close_note(self, db_session_factory):
        """挂起自动关闭：auto_close 事件 + 备注"超时自动关闭"。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session, status="PENDING_VERIFY")
            _apply(session, ticket_id, "auto_close", "system", note="超时自动关闭")
            assert _get(session, ticket_id).status == "CLOSED"
            logs = _logs(session, ticket_id)
            assert logs[-1].note == "超时自动关闭"
            assert logs[-1].actor == "system"


class TestIllegalTransitions:
    def test_illegal_raises_and_rolls_back(self, db_session_factory):
        """非法跳转：抛 TransitionError，状态与日志零残留（整体回滚）。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session)  # SUBMITTED
            with pytest.raises(TransitionError):
                _apply(session, ticket_id, "complete", "system")  # SUBMITTED→PENDING_VERIFY 非法
            assert _get(session, ticket_id).status == "SUBMITTED"
            assert _logs(session, ticket_id) == []

    def test_cancel_after_in_progress_illegal(self, db_session_factory):
        """维修中不可撤：IN_PROGRESS 下 cancel 非法（需求 §3 已拍板）。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session, status="IN_PROGRESS")
            with pytest.raises(TransitionError):
                _apply(session, ticket_id, "cancel", "student-001")
            assert _get(session, ticket_id).status == "IN_PROGRESS"

    def test_terminal_states_immutable(self, db_session_factory):
        """终态不可再跳转：CLOSED/CANCELLED 无出边。"""
        with db_session_factory() as session:
            for status in ("CLOSED", "CANCELLED"):
                ticket_id = _create_ticket(session, status=status)
                with pytest.raises(TransitionError):
                    _apply(session, ticket_id, "assign", "admin-001")
                assert _get(session, ticket_id).status == status

    def test_missing_ticket_raises(self, db_session_factory):
        """工单不存在抛 TicketNotFound（携带 ticket_id）。"""
        with db_session_factory() as session:
            with pytest.raises(TicketNotFound) as exc:
                _apply(session, 99999, "assign", "admin-001")
            assert exc.value.ticket_id == 99999


class TestEscalationFields:
    def test_escalate_increments_and_stays_in_status(self, db_session_factory):
        """超时升级=字段不是状态：计数 +1、置 escalated_at，工单留在原状态。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session)
            _apply(session, ticket_id, "assign", "admin-001")
            _apply(session, ticket_id, "start", "rm-001")
            _apply(session, ticket_id, "complete", "system", escalate=True)
            got = _get(session, ticket_id)
            assert got.escalation_count == 1
            assert got.escalated_at is not None
            assert got.status == "PENDING_VERIFY"  # 原状态继续流转

    def test_escalate_no_extra_log(self, db_session_factory):
        """升级不是状态跳转：不产生额外审计日志（日志只记真正的状态变化）。"""
        with db_session_factory() as session:
            ticket_id = _create_ticket(session)
            _apply(session, ticket_id, "assign", "admin-001")
            _apply(session, ticket_id, "start", "rm-001", escalate=True)
            assert len(_logs(session, ticket_id)) == 2  # assign + start，无第三条

    def test_ticket_not_found_rolls_back(self, db_session_factory):
        """SAVEPOINT 回滚边界：异常路径不残留任何写入。"""
        with db_session_factory() as session:
            before = _logs(session, 1)
            with pytest.raises(TicketNotFound):
                _apply(session, 1, "assign", "admin-001")
            assert _logs(session, 1) == before
