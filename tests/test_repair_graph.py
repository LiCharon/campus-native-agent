"""RepairGraph 测试（M3 核心）：采集追问/分类确认/建单派单/多轮恢复。

注入设施：FakeFieldExtractor / FakeRepairClassifier（conftest）+ InMemorySaver
（checkpointer 必传，interrupt 需持久化；终态 thread 复用行为已实测规避）。
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from campus_desk.db.models import Ticket, TicketLog
from campus_desk.repair.classify import ClassificationResult
from campus_desk.repair.drafting import DraftExtract
from campus_desk.repair.graph import build_repair_graph
from tests.conftest import FakeFieldExtractor, FakeRepairClassifier

CFG = {"configurable": {"thread_id": "t1"}}


def _build(db_session_factory, extractor, classifier):
    return build_repair_graph(
        db_session_factory,
        extractor=extractor,
        classifier=classifier,
        checkpointer=InMemorySaver(),
        user_id="student-001",
        actor="student-001",
        default_contact="李华",
    )


def _full_extract():
    """完整信息抽取（一轮建单场景）。"""
    return FakeFieldExtractor(
        default=DraftExtract(description="", building="3号楼", room="502", contact="李华")
    )


def _p2_classifier(confidence: float = 0.9, needs_confirm: bool = False):
    return FakeRepairClassifier(
        default=ClassificationResult(
            category="水电",
            priority="P2",
            confidence=confidence,
            needs_human_confirm=needs_confirm,
            reason="规则/LLM 判定",
        )
    )


class TestOneShotTicket:
    """信息齐全一轮建单 + 自动派单。"""

    def test_full_flow_to_assigned(self, db_session_factory):
        graph = _build(db_session_factory, _full_extract(), _p2_classifier())
        out = graph.invoke({"user_input": "3号楼502灯管闪烁，联系人李华"}, CFG)
        assert out["finished"] is True
        assert out["ticket_status"] == "ASSIGNED"
        assert out["ticket_id"] == 1
        assert out["repairman"]["trade"] == "水电"  # 水电类 → 后勤·水电
        assert out["reply"] and "已创建并派给" in out["reply"]
        assert "create_ticket" in out["tool_calls"]
        assert "update_ticket_status" in out["tool_calls"]
        assert "SUBMITTED->ASSIGNED" in out["status_events"]
        # 落库核验：状态 + 派单 + 审计日志（SUBMITTED→ASSIGNED）
        with db_session_factory() as session, session.begin():
            t = session.get(Ticket, 1)
            assert t.status == "ASSIGNED"
            assert t.repairman_id == "rm-001"  # 在岗水电第一人
            assert t.building == "3号楼"
            assert t.location == "502室"
            assert t.contact == "李华"
            assert t.priority == "P2"  # 普通单落库 P2（默认值）
            logs = session.query(TicketLog).filter(TicketLog.ticket_id == 1).all()
            assert len(logs) == 1  # 仅 ASSIGNED 一跳（建单非状态跳转）
            assert logs[0].from_status == "SUBMITTED" and logs[0].to_status == "ASSIGNED"
            assert logs[0].note == "自动派单给 陈师傅"

    def test_no_interrupt_on_complete_info(self, db_session_factory):
        """信息齐全不中断：一轮结束，无 pending。"""
        graph = _build(db_session_factory, _full_extract(), _p2_classifier())
        graph.invoke({"user_input": "3号楼502灯管闪烁，联系人李华"}, CFG)
        state = graph.get_state(CFG)
        assert state.next == ()  # 终态


class TestCollectQuestions:
    """采集追问：缺啥问啥 ≤2 轮。"""

    def test_missing_building_asks_once(self, db_session_factory):
        """缺楼栋 → 追问 1 轮（resume 补上后建单）。"""
        extractor = FakeFieldExtractor(
            sequence=[
                DraftExtract(description="", building=None, room="502", contact="李华"),
                DraftExtract(description="", building="3号楼", room=None, contact=None),
            ]
        )
        graph = _build(db_session_factory, extractor, _p2_classifier())
        out = graph.invoke({"user_input": "502室灯管闪烁，联系人李华"}, CFG)
        # 追问轮：pending 等待中，尚未建单
        assert out["pending_question"] and "楼栋" in out["pending_question"]
        assert out["pending_stage"] == "collect"
        assert out.get("ticket_id") is None
        assert "ask_collect" in out["tool_calls"]
        # 学生回复补楼栋 → resume 续跑
        out2 = graph.invoke(Command(resume="3号楼"), CFG)
        assert out2["finished"] is True
        assert out2["ticket_status"] == "ASSIGNED"
        assert out2["draft"]["building"] == "3号楼"
        # 只追问 1 轮（rounds=1）
        assert out2["draft"]["rounds"] == 1

    def test_max_two_rounds_then_creates_with_default_contact(self, db_session_factory):
        """追问 ≤2 轮后带缺项建单（contact 用 default_contact 兜底）。"""
        extractor = FakeFieldExtractor(
            default=DraftExtract(description="", building="3号楼", room=None, contact=None)
        )
        graph = _build(db_session_factory, extractor, _p2_classifier())
        out = graph.invoke({"user_input": "3号楼灯管闪烁"}, CFG)
        assert "联系人" in out["pending_question"]  # 第 1 轮问
        out2 = graph.invoke(Command(resume="李华"), CFG)
        assert out2["pending_question"] is not None  # contact 规则抽取 None → 再问（第 2 轮）
        out3 = graph.invoke(Command(resume="李华"), CFG)
        assert out3["finished"] is True  # 第 2 轮后不再追问，带缺项建单
        assert out3["ticket_status"] == "ASSIGNED"
        assert out3["draft"]["rounds"] == 2

    def test_contact_from_llm_extract_no_question(self, db_session_factory):
        """联系人被 LLM 抽到 → 不问（用户拍板：固定信息一次性收集）。"""
        extractor = _full_extract()
        graph = _build(db_session_factory, extractor, _p2_classifier())
        out = graph.invoke({"user_input": "3号楼502灯管闪烁"}, CFG)  # 描述里没提联系人
        assert out["finished"] is True  # 抽取器已给 contact，不再追问
        assert out["draft"]["contact"] == "李华"


class TestClassifyConfirm:
    def test_low_confidence_confirm_round(self, db_session_factory):
        """低置信 → 确认轮（pending_stage=classify）→ 确认后建单。"""
        graph = _build(
            db_session_factory, _full_extract(), _p2_classifier(confidence=0.4, needs_confirm=True)
        )
        out = graph.invoke({"user_input": "3号楼502灯管闪烁，联系人李华"}, CFG)
        assert out["pending_question"] and "归为" in out["pending_question"]
        assert out["pending_stage"] == "classify"
        assert out.get("ticket_id") is None
        out2 = graph.invoke(Command(resume="对"), CFG)
        assert out2["finished"] is True
        assert out2["ticket_status"] == "ASSIGNED"
        assert "ask_confirm" in out2["tool_calls"]

    def test_confirm_dispute_marks_human_review(self, db_session_factory):
        """学生异议（"不对"）→ 标记人工复核仍建单（不阻塞闭环）。"""
        graph = _build(
            db_session_factory, _full_extract(), _p2_classifier(confidence=0.4, needs_confirm=True)
        )
        graph.invoke({"user_input": "3号楼502灯管闪烁，联系人李华"}, CFG)
        out2 = graph.invoke(Command(resume="不对，是设备问题"), CFG)
        assert out2["finished"] is True
        assert "异议" in out2["classification"]["reason"]

    def test_high_confidence_no_confirm(self, db_session_factory):
        """高置信不确认：一轮直建单。"""
        graph = _build(db_session_factory, _full_extract(), _p2_classifier(confidence=0.9))
        out = graph.invoke({"user_input": "3号楼502灯管闪烁，联系人李华"}, CFG)
        assert out["finished"] is True
        assert "ask_confirm" not in out["tool_calls"]


class TestSessionSemantics:
    def test_new_thread_new_session(self, db_session_factory):
        """新 thread_id = 新报修会话（终态 thread 复用已实测产生残留+重复中断）。

        用规则版注入（真抽取/分类）：门锁 → 门窗类 → 后勤·门窗（rm-004）。
        """
        from campus_desk.repair.classify import RepairClassifier
        from campus_desk.repair.drafting import FieldExtractor

        graph = build_repair_graph(
            db_session_factory,
            extractor=FieldExtractor(llm=None),
            classifier=RepairClassifier(llm=None),
            checkpointer=InMemorySaver(),
        )
        # 会话 1：规则抽取抽不到 contact → 追问 2 轮后建单 #1
        graph.invoke({"user_input": "3号楼502灯管闪烁，联系人李华"}, CFG)
        graph.invoke(Command(resume="李华"), CFG)
        out1 = graph.invoke(Command(resume="李华"), CFG)
        assert out1["ticket_id"] == 1
        # 会话 2：新 thread_id → 独立会话，建单 #2（不残留会话 1 的 state）
        cfg2 = {"configurable": {"thread_id": "t2"}}
        graph.invoke({"user_input": "6号楼601门锁坏了，联系人王芳"}, cfg2)
        graph.invoke(Command(resume="王芳"), cfg2)
        out2 = graph.invoke(Command(resume="王芳"), cfg2)
        assert out2["ticket_id"] == 2
        with db_session_factory() as session, session.begin():
            t2 = session.get(Ticket, 2)
            assert t2.description == "6号楼601门锁坏了，联系人王芳"
            assert t2.repairman_id == "rm-004"  # 门窗类 → 郑师傅（在岗）

    def test_get_state_next_empty_at_terminal(self, db_session_factory):
        """终态 get_state().next 为空（orchestrator 判定 resume 的依据）。"""
        graph = _build(db_session_factory, _full_extract(), _p2_classifier())
        graph.invoke({"user_input": "3号楼502灯管闪烁，联系人李华"}, CFG)
        assert graph.get_state(CFG).next == ()


class TestPriorityPropagation:
    """M5 修复：分类定级结果必须落库（P1 安全单按 4h 升级阈值）。

    断链背景：M3 起 classify 判 P1 只标记 needs_human_confirm，建单恒落 P2，
    升级扫描的 P1 阈值对报修单永不生效（M5 后该字段才被消费）。
    """

    def test_p1_classification_persisted(self, db_session_factory):
        """分类器判 P1（安全规则）→ 工单落库 priority=P1，升级按 4h 阈值。"""
        graph = _build(
            db_session_factory,
            _full_extract(),
            FakeRepairClassifier(
                default=ClassificationResult(
                    category="水电",
                    priority="P1",
                    confidence=0.9,
                    needs_human_confirm=True,  # P1 安全单同时要求人工确认（M3 行为不变）
                    reason="安全规则命中",
                )
            ),
        )
        # P1 安全单必须过人工确认轮（M3 语义：needs_human_confirm）→ 学生确认后放行
        graph.invoke({"user_input": "3号楼502漏水严重，联系人李华"}, CFG)
        assert graph.get_state(CFG).next != ()  # 停在确认轮
        graph.invoke(Command(resume="好"), CFG)
        with db_session_factory() as session, session.begin():
            t = session.get(Ticket, 1)
            assert t.priority == "P1"
            assert t.status == "ASSIGNED"  # 派单不受影响
