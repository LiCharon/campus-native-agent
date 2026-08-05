"""投诉管道测试（M5-T2）：复用 RepairGraph（ticket_type="complaint"）建 P1 投诉单。

投诉语义（与报修的差异点，逐条锁定）：
- 必填集仅 contact（不追问楼栋）；跳过 classify（无确认轮、不调分类器）
- create 建 complaint 单停 SUBMITTED：不自动派单（repairman=None）、
  不更新报修画像（user_profiles 无新行）
- 无实质描述（<4 字，如"我投诉"）转人工不建单（handoff_reject）
- location 透传为投诉对象/位置（如"食堂阿姨"）

注入设施：FakeFieldExtractor（DraftExtract 序列）+ FakeRepairClassifier
（永不调用，防误入 classify）+ InMemorySaver。FakeFieldExtractor 不动
conftest——DraftExtract 新增 location 字段后序列项直接支持。
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from campus_desk.consult.decide import ConsultDecision
from campus_desk.consult.graph import build_consult_graph
from campus_desk.db.models import Ticket, UserProfile
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.intent import IntentResult
from campus_desk.entry.orchestrator import turn
from campus_desk.repair.classify import ClassificationResult
from campus_desk.repair.drafting import DraftExtract
from campus_desk.repair.graph import build_repair_graph
from tests.conftest import (
    FakeConsultDecider,
    FakeFieldExtractor,
    FakeIntentClassifier,
    FakeRepairClassifier,
)

CFG = {"configurable": {"thread_id": "c1"}}


def _build(db_session_factory, extractor):
    """投诉图（ticket_type="complaint"）。classifier 注入 Fake 防误入 classify。"""
    return build_repair_graph(
        db_session_factory,
        extractor=extractor,
        classifier=FakeRepairClassifier(),
        checkpointer=InMemorySaver(),
        user_id="student-001",
        actor="student-001",
        default_contact="李华",
        ticket_type="complaint",
    )


def _full_extract():
    """全信息抽取（contact + location，一轮建单场景）。"""
    return FakeFieldExtractor(
        default=DraftExtract(
            description="", building=None, room=None, contact="李华", location="食堂阿姨"
        )
    )


def _ticket(db_session_factory, ticket_id: int) -> Ticket:
    with db_session_factory() as session, session.begin():
        t = session.get(Ticket, ticket_id)
        session.expunge(t)
        return t


class TestComplaintGraph:
    def test_full_info_creates_complaint_ticket(self, db_session_factory):
        """全信息一轮建单：complaint 单落库、停 SUBMITTED、不派单、不更新画像。"""
        graph = _build(db_session_factory, _full_extract())
        out = graph.invoke({"user_input": "食堂阿姨打饭态度差，我要投诉"}, CFG)
        assert out["finished"] is True
        assert out["ticket_id"] == 1
        assert out["ticket_status"] == "SUBMITTED"
        assert out["repairman"] is None  # 不自动派单
        assert "投诉单" in out["reply"]
        assert "报修" not in out["reply"] and "派给" not in out["reply"]
        assert out["tool_calls"] == ["create_ticket"]  # 无 update_ticket_status
        assert out["status_events"] == ["SUBMITTED"]
        # 落库核验：type/building/状态/维修工
        t = _ticket(db_session_factory, 1)
        assert t.ticket_type == "complaint"
        assert t.building is None  # 投诉不填楼栋
        assert t.status == "SUBMITTED"
        assert t.repairman_id is None
        assert t.contact == "李华"
        assert t.priority == "P1"  # 需求拍死：投诉 = P1 工单（管道固定传 P1）
        # 画像门控：投诉不污染报修画像（无新行）
        with db_session_factory() as session, session.begin():
            assert session.get(UserProfile, "student-001") is None

    def test_missing_contact_asks_then_resume(self, db_session_factory):
        """缺 contact → 追问 1 轮（文案不含"楼栋"）→ resume 补上后建单。"""
        extractor = FakeFieldExtractor(
            sequence=[
                DraftExtract(
                    description="", building=None, room=None, contact=None, location="食堂阿姨"
                ),
                DraftExtract(
                    description="", building=None, room=None, contact="李华", location=None
                ),
            ]
        )
        graph = _build(db_session_factory, extractor)
        out = graph.invoke({"user_input": "食堂阿姨打饭态度差，我要投诉"}, CFG)
        assert out["pending_question"] and "联系人" in out["pending_question"]
        assert "楼栋" not in out["pending_question"]  # 投诉不追问楼栋
        assert out["pending_stage"] == "collect"
        assert out.get("ticket_id") is None
        assert "ask_collect" in out["tool_calls"]
        out2 = graph.invoke(Command(resume="李华"), CFG)
        assert out2["finished"] is True
        assert out2["ticket_status"] == "SUBMITTED"
        assert out2["draft"]["contact"] == "李华"
        assert out2["draft"]["rounds"] == 1

    def test_location_passed_to_ticket(self, db_session_factory):
        """location 透传：抽取到的投诉对象/位置写入 tickets.location。"""
        graph = _build(db_session_factory, _full_extract())
        graph.invoke({"user_input": "食堂阿姨打饭态度差，我要投诉"}, CFG)
        t = _ticket(db_session_factory, 1)
        assert t.location == "食堂阿姨"
        assert t.description == "食堂阿姨打饭态度差，我要投诉"

    def test_no_substance_rejects_to_human(self, db_session_factory):
        """无实质（"我投诉"3 字）：首轮追问 contact → resume 仍无实质 →
        转人工不建单（handoff_reject、无 create_ticket、无落库）。"""
        extractor = FakeFieldExtractor(
            sequence=[
                DraftExtract(description="", building=None, room=None, contact=None),
                DraftExtract(description="", building=None, room=None, contact="李华"),
            ]
        )
        graph = _build(db_session_factory, extractor)
        out = graph.invoke({"user_input": "我投诉"}, CFG)
        assert out["pending_question"]  # 首轮仍先问联系人（剧本可断言）
        assert out["pending_stage"] == "collect"
        out2 = graph.invoke(Command(resume="李华"), CFG)
        assert out2["finished"] is True
        assert "handoff_reject" in out2["tool_calls"]
        assert "create_ticket" not in out2["tool_calls"]
        assert out2.get("ticket_id") is None
        assert out2.get("ticket_status") is None
        assert "转人工" in out2["reply"]
        with db_session_factory() as session, session.begin():
            assert session.query(Ticket).count() == 0  # 不建单

    def test_short_but_substantive_creates(self, db_session_factory):
        """有实质但短（"食堂态度差"5 字 ≥ 4 阈值）→ 正常建单。"""
        graph = _build(db_session_factory, _full_extract())
        out = graph.invoke({"user_input": "食堂态度差"}, CFG)
        assert out["finished"] is True
        assert out["ticket_id"] == 1
        assert "投诉单" in out["reply"]
        assert "handoff_reject" not in out["tool_calls"]


class TestComplaintOrchestrator:
    def _entry(self, intent: str):
        return build_entry_graph(
            FakeIntentClassifier(
                IntentResult(intent=intent, confidence=0.9, secondary_intents=[], reason="测试")
            )
        )

    def _consult(self, db_session_factory):
        return build_consult_graph(
            db_session_factory,
            decider=FakeConsultDecider(
                default=ConsultDecision(action="answer", reply="教务密码可在教务系统重置。")
            ),
            checkpointer=InMemorySaver(),
        )

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

    def _complaint(self, db_session_factory):
        return build_repair_graph(
            db_session_factory,
            extractor=_full_extract(),
            classifier=FakeRepairClassifier(),
            checkpointer=InMemorySaver(),
            ticket_type="complaint",
        )

    def test_complaint_route_creates_ticket(self, db_session_factory):
        """COMPLAINT 路由 → 进 complaint_graph 建投诉单（route 保持 complaint）。"""
        cg = self._consult(db_session_factory)
        rg = self._repair(db_session_factory)
        comp = self._complaint(db_session_factory)
        out = turn(
            self._entry("complaint"),
            rg,
            cg,
            "case-c1",
            "食堂阿姨打饭态度差，我要投诉",
            complaint_graph=comp,
        )
        assert out["route"] == "complaint"
        assert out["ticket_id"] == 1
        assert out["ticket_status"] == "SUBMITTED"
        assert "投诉单" in out["reply"]
        t = _ticket(db_session_factory, 1)
        assert t.ticket_type == "complaint"
        assert t.priority == "P1"

    def test_pending_other_reply_resumes_complaint(self, db_session_factory):
        """投诉挂起时 other 类输入（"李华"）→ resume 进 complaint_graph，
        不落人工占位（M3/M4 同源坑：Entry 无上下文判 other）。"""
        extractor = FakeFieldExtractor(
            sequence=[
                DraftExtract(description="", building=None, room=None, contact=None),
                DraftExtract(description="", building=None, room=None, contact="李华"),
            ]
        )
        cg = self._consult(db_session_factory)
        rg = self._repair(db_session_factory)
        comp = _build(db_session_factory, extractor)
        out1 = turn(
            self._entry("complaint"),
            rg,
            cg,
            "case-c2",
            "食堂阿姨态度差，我要投诉",
            complaint_graph=comp,
        )
        assert out1["pending_question"]  # 联系人追问挂起
        out2 = turn(self._entry("other"), rg, cg, "case-c2", "李华", complaint_graph=comp)
        assert out2["route"] == "complaint"  # 挂起中 other → 仍进投诉
        assert out2["finished"] is True
        assert out2["ticket_id"] == 1
        assert out2["reply"].startswith("您的投诉单")

    def test_repair_and_complaint_threads_isolated(self, db_session_factory):
        """同 thread_id 下报修会话与投诉会话互不干扰（各自图作用域独立）。"""
        cg = self._consult(db_session_factory)
        rg = self._repair(db_session_factory)
        comp = self._complaint(db_session_factory)
        tid = "case-c3"
        out_r = turn(self._entry("repair"), rg, cg, tid, "3号楼502灯坏了，李华")
        assert out_r["ticket_id"] == 1  # 报修单 #1
        out_c = turn(
            self._entry("complaint"),
            rg,
            cg,
            tid,
            "食堂阿姨态度差，我要投诉",
            complaint_graph=comp,
        )
        assert out_c["ticket_id"] == 2  # 投诉单 #2（独立会话，不残留报修 state）
        assert out_c["ticket_status"] == "SUBMITTED"
        t1 = _ticket(db_session_factory, 1)
        t2 = _ticket(db_session_factory, 2)
        assert t1.ticket_type == "repair" and t1.status == "ASSIGNED"
        assert t2.ticket_type == "complaint" and t2.status == "SUBMITTED"

    def test_no_complaint_graph_falls_back_to_placeholder(self, db_session_factory):
        """complaint_graph 未注入（旧调用方）→ COMPLAINT 仍走占位回复。"""
        cg = self._consult(db_session_factory)
        rg = self._repair(db_session_factory)
        out = turn(self._entry("complaint"), rg, cg, "case-c4", "食堂阿姨态度差，我要投诉")
        assert out["route"] == "complaint"
        assert "升级处理" in out["reply"]  # 占位文案（来自 entry 图）
        assert out.get("ticket_id") is None
