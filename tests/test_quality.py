"""QualityAgent 测试（M4，需求 §6）：closed_at 写入 / 待回访查询 / 满意度采集 / 编排触发。

注入：InMemorySaver（QualityGraph interrupt 持久化）+ db_session_factory
（SQLite 内存库，直造 CLOSED 工单测 pending 判定）。
"""

from datetime import UTC, datetime, timedelta

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from campus_desk.consult.decide import ConsultDecision
from campus_desk.db.models import Ticket
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.intent import IntentResult
from campus_desk.entry.orchestrator import turn
from campus_desk.quality.graph import build_quality_graph
from campus_desk.quality.pending import find_pending_reviews
from campus_desk.repair.classify import ClassificationResult
from campus_desk.repair.drafting import DraftExtract
from campus_desk.repair.graph import build_repair_graph
from campus_desk.state_machine.transitions import apply_transition
from tests.conftest import (
    FakeConsultDecider,
    FakeFieldExtractor,
    FakeIntentClassifier,
    FakeRepairClassifier,
)

CFG = {"configurable": {"thread_id": "quality-t1"}}
QUALITY_CFG = {"configurable": {"thread_id": "quality-quality-t1"}}


def _closed_ticket(db_session_factory, user_id="student-001", hours_ago: float = 25) -> int:
    """直造 CLOSED 工单（绕过状态机，测 pending 判定；closed_at 可控）。"""
    with db_session_factory() as session, session.begin():
        t = Ticket(
            user_id=user_id,
            ticket_type="repair",
            description="灯坏了",
            contact="李华",
            category="水电",
            priority="P2",
            status="CLOSED",
            closed_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        )
        session.add(t)
        session.flush()
        return t.id


def _quality(db_session_factory):
    return build_quality_graph(db_session_factory, checkpointer=InMemorySaver())


class TestClosedAtWrite:
    def test_verify_ok_writes_closed_at(self, db_session_factory):
        """验收通过（verify_ok → CLOSED）→ closed_at 自动写入（唯一写入口）。"""
        with db_session_factory() as session, session.begin():
            t = Ticket(
                user_id="student-001",
                ticket_type="repair",
                description="灯坏了",
                contact="李华",
                category="水电",
                priority="P2",
                status="PENDING_VERIFY",
            )
            session.add(t)
            session.flush()
            tid = t.id
            apply_transition(session, tid, "verify_ok", actor="student-001")
        with db_session_factory() as session:
            assert session.get(Ticket, tid).closed_at is not None

    def test_non_closed_no_closed_at(self, db_session_factory):
        """未进 CLOSED 不写 closed_at。"""
        with db_session_factory() as session, session.begin():
            t = Ticket(
                user_id="student-001",
                ticket_type="repair",
                description="灯坏了",
                contact="李华",
                category="水电",
                priority="P2",
                status="SUBMITTED",
            )
            session.add(t)
            session.flush()
            tid = t.id
        with db_session_factory() as session:
            assert session.get(Ticket, tid).closed_at is None


class TestFindPendingReviews:
    def test_pending_after_24h(self, db_session_factory):
        tid = _closed_ticket(db_session_factory, hours_ago=25)
        pending = find_pending_reviews(db_session_factory, "student-001")
        assert [p["ticket_id"] for p in pending] == [tid]

    def test_recently_closed_not_pending(self, db_session_factory):
        _closed_ticket(db_session_factory, hours_ago=2)
        assert find_pending_reviews(db_session_factory, "student-001") == []

    def test_reviewed_not_pending(self, db_session_factory):
        _closed_ticket(db_session_factory, hours_ago=25)
        with db_session_factory() as session, session.begin():
            t = session.query(Ticket).first()
            t.reviewed_at = datetime.now(UTC)
        assert find_pending_reviews(db_session_factory, "student-001") == []

    def test_wrong_user_not_pending(self, db_session_factory):
        _closed_ticket(db_session_factory, user_id="student-002", hours_ago=25)
        assert find_pending_reviews(db_session_factory, "student-001") == []

    def test_no_tickets(self, db_session_factory):
        assert find_pending_reviews(db_session_factory, "student-001") == []


class TestQualityGraph:
    def test_remind_then_collect_rating(self, db_session_factory):
        """提醒轮 → 学生评分 5 → 写库（rating + reviewed_at），感谢结束。"""
        tid = _closed_ticket(db_session_factory)
        graph = _quality(db_session_factory)
        out1 = graph.invoke(
            {
                "user_input": "随便说点什么",
                "pending_tickets": [{"ticket_id": tid, "description": "灯坏了"}],
            },
            CFG,
        )
        assert out1["outcome"] == "remind"
        assert out1["pending_question"] and "工单" in out1["pending_question"]
        assert graph.get_state(CFG).next == ("wait",)

        out2 = graph.invoke(Command(resume="5"), CFG)
        assert out2["outcome"] == "collected"
        assert out2["finished"] is True
        assert "谢谢" in out2["reply"]
        with db_session_factory() as session:
            t = session.get(Ticket, tid)
            assert t.rating == 5
            assert t.reviewed_at is not None

    def test_text_answer_stored_as_comment(self, db_session_factory):
        """非数字回答 → review_comment 落库（宽松收，rating 空）。"""
        tid = _closed_ticket(db_session_factory)
        graph = _quality(db_session_factory)
        graph.invoke(
            {"user_input": "x", "pending_tickets": [{"ticket_id": tid, "description": "灯坏了"}]},
            CFG,
        )
        graph.invoke(Command(resume="修得很快，师傅态度好"), CFG)
        with db_session_factory() as session:
            t = session.get(Ticket, tid)
            assert t.rating is None
            assert "修得很快" in t.review_comment

    def test_low_rating_encourages(self, db_session_factory):
        """低分（<4）→ 回复含改进承诺。"""
        tid = _closed_ticket(db_session_factory)
        graph = _quality(db_session_factory)
        graph.invoke(
            {"user_input": "x", "pending_tickets": [{"ticket_id": tid, "description": "灯坏了"}]},
            CFG,
        )
        out = graph.invoke(Command(resume="2"), CFG)
        assert "改进" in out["reply"]

    def test_mixed_rating_and_comment(self, db_session_factory):
        """ "4分，整体不错" → rating=4 + comment 都落库。"""
        tid = _closed_ticket(db_session_factory)
        graph = _quality(db_session_factory)
        graph.invoke(
            {"user_input": "x", "pending_tickets": [{"ticket_id": tid, "description": "灯坏了"}]},
            CFG,
        )
        graph.invoke(Command(resume="4分，整体不错"), CFG)
        with db_session_factory() as session:
            t = session.get(Ticket, tid)
            assert t.rating == 4
            assert "整体不错" in t.review_comment


class TestOrchestratorQuality:
    def _entry(self, intent="repair", confidence=0.9):
        clf = FakeIntentClassifier(
            IntentResult(intent=intent, confidence=confidence, secondary_intents=[], reason="测试")
        )
        return build_entry_graph(classifier=clf)

    def _repair(self, db_session_factory):
        return build_repair_graph(
            db_session_factory,
            extractor=FakeFieldExtractor(
                default=DraftExtract(description="", building="3号楼", room="502", contact="李华")
            ),
            classifier=FakeRepairClassifier(
                default=ClassificationResult(category="水电", priority="P2", confidence=0.9)
            ),
            checkpointer=InMemorySaver(),
        )

    def _consult(self, db_session_factory):
        from campus_desk.consult.graph import build_consult_graph

        return build_consult_graph(
            db_session_factory,
            decider=FakeConsultDecider(default=ConsultDecision(action="answer", reply="ok")),
            checkpointer=InMemorySaver(),
        )

    def test_quality_remind_takes_priority(self, db_session_factory):
        """有待回访工单 → 学生进对话先收到回访提醒（route=quality）。"""
        tid = _closed_ticket(db_session_factory)
        qg = _quality(db_session_factory)
        out = turn(
            self._entry(),
            self._repair(db_session_factory),
            self._consult(db_session_factory),
            "q-1",
            "灯坏了",
            quality_graph=qg,
            user_id="student-001",
            session_factory=db_session_factory,
        )
        assert out["route"] == "quality"
        assert out["outcome"] == "remind"
        assert str(tid) in out["reply"]

    def test_quality_resume_then_main_flow(self, db_session_factory):
        """评分完成后 → 下一轮正常主流程（已回访不再提醒）。"""
        _closed_ticket(db_session_factory)
        qg = _quality(db_session_factory)
        entry, rg, cg = (
            self._entry(),
            self._repair(db_session_factory),
            self._consult(db_session_factory),
        )
        out1 = turn(
            entry,
            rg,
            cg,
            "q-2",
            "你好",
            quality_graph=qg,
            user_id="student-001",
            session_factory=db_session_factory,
        )
        assert out1["route"] == "quality"
        out2 = turn(
            entry,
            rg,
            cg,
            "q-2",
            "5",
            quality_graph=qg,
            user_id="student-001",
            session_factory=db_session_factory,
        )
        assert out2["route"] == "quality"
        assert out2["outcome"] == "collected"
        # 已回访 → 下一轮走主流程
        out3 = turn(
            entry,
            rg,
            cg,
            "q-2",
            "3号楼502灯坏了",
            quality_graph=qg,
            user_id="student-001",
            session_factory=db_session_factory,
        )
        assert out3["route"] == "repair"
        assert out3["ticket_id"] is not None

    def test_no_user_id_skips_quality(self, db_session_factory):
        """无 user_id（评测/无身份）→ 跳过回访检查，直接主流程。"""
        _closed_ticket(db_session_factory)
        qg = _quality(db_session_factory)
        out = turn(
            self._entry("repair"),
            self._repair(db_session_factory),
            self._consult(db_session_factory),
            "q-3",
            "3号楼502灯坏了",
            quality_graph=qg,
        )
        assert out["route"] == "repair"
        assert out["ticket_id"] is not None
