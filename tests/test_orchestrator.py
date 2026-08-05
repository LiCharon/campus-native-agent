"""编排层测试（M3 Repair + M4 Consult）：每轮先 Entry 后下游图；resume 判定；多意图不吞会话。

注入：FakeIntentClassifier（Entry）+ FakeFieldExtractor/FakeRepairClassifier
（RepairGraph）+ FakeConsultDecider（ConsultGraph）+ InMemorySaver。
"""

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.consult.decide import ConsultDecision
from campus_desk.consult.graph import build_consult_graph
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

TID = "case-001"


def _entry(intent: str, confidence: float = 0.9, secondary=None):
    clf = FakeIntentClassifier(
        IntentResult(
            intent=intent, confidence=confidence, secondary_intents=secondary or [], reason="测试"
        )
    )
    return build_entry_graph(classifier=clf)


def _repair(db_session_factory, extractor=None, classifier=None):
    return build_repair_graph(
        db_session_factory,
        extractor=extractor
        or FakeFieldExtractor(
            default=DraftExtract(description="", building="3号楼", room="502", contact="李华")
        ),
        classifier=classifier
        or FakeRepairClassifier(
            default=ClassificationResult(category="水电", priority="P2", confidence=0.9)
        ),
        checkpointer=InMemorySaver(),
    )


def _consult(db_session_factory, decider=None):
    return build_consult_graph(
        db_session_factory,
        decider=decider
        or FakeConsultDecider(
            default=ConsultDecision(action="answer", reply="教务密码可在教务系统点忘记密码重置。")
        ),
        checkpointer=InMemorySaver(),
        student_no="2024001",
    )


class TestRepairFlow:
    def test_first_turn_creates_ticket(self, db_session_factory):
        """报修首轮：Entry 分流 → RepairGraph 建单派单。"""
        cg = _consult(db_session_factory)
        out = turn(_entry("repair"), _repair(db_session_factory), cg, TID, "3号楼502灯坏了，李华")
        assert out["route"] == "repair"
        assert out["ticket_id"] == 1
        assert out["ticket_status"] == "ASSIGNED"
        assert "已创建并派给" in out["reply"]

    def test_resume_after_question(self, db_session_factory):
        """追问挂起后：下一轮 Entry 仍 repair → get_state().next 判定 resume 续跑。"""
        extractor = FakeFieldExtractor(
            sequence=[
                DraftExtract(description="", building=None, room="502", contact="李华"),
                DraftExtract(description="", building="3号楼", room=None, contact=None),
            ]
        )
        rg = _repair(db_session_factory, extractor=extractor)
        cg = _consult(db_session_factory)
        out1 = turn(_entry("repair"), rg, cg, TID, "502室灯坏了，李华")
        assert out1["pending_question"] and "楼栋" in out1["pending_question"]
        out2 = turn(_entry("repair"), rg, cg, TID, "3号楼")
        assert out2["finished"] is True
        assert out2["ticket_id"] == 1


class TestNonRepairRoutes:
    def test_consult_goes_to_graph(self, db_session_factory):
        """咨询路由（M4 实装）→ ConsultGraph 决策，不触发 RepairGraph。"""
        cg = _consult(db_session_factory)
        out = turn(_entry("consult"), _repair(db_session_factory), cg, TID, "密码怎么改")
        assert out["route"] == "consult"
        assert out["outcome"] == "answer"
        assert "密码" in out["reply"]
        assert out.get("ticket_id") is None  # 不触发 RepairGraph

    def test_consult_ask_then_resume(self, db_session_factory):
        """咨询追问挂起 → 下一轮 Entry 仍 consult → resume 续跑。"""
        cg = _consult(
            db_session_factory,
            FakeConsultDecider(
                sequence=[
                    ConsultDecision(
                        action="ask", questions=["您的学号是多少？"], reply="请补充学号"
                    ),
                    ConsultDecision(action="answer", reply="账号状态正常。"),
                ]
            ),
        )
        out1 = turn(_entry("consult"), _repair(db_session_factory), cg, TID, "查下我账号")
        assert out1["outcome"] == "ask"
        assert out1["pending_question"] and "学号" in out1["pending_question"]
        out2 = turn(_entry("consult"), _repair(db_session_factory), cg, TID, "2024001")
        assert out2["outcome"] == "answer"
        assert out2["finished"] is True

    def test_handoff_placeholder(self, db_session_factory):
        cg = _consult(db_session_factory)
        out = turn(_entry("other", confidence=0.4), _repair(db_session_factory), cg, TID, "你好")
        assert out["route"] == "human_handoff"
        assert "人工" in out["reply"]


class TestMultiIntentNotSwallowed:
    def test_side_question_during_repair_goes_to_consult(self, db_session_factory):
        """报修中插咨询（"顺便问密码"）：Entry 每轮重路由 → 咨询侧真实处理，
        报修会话保持挂起不被吞（两次调用设计的核心价值）。"""
        # 首轮：报修（含次要咨询意图）→ 进 RepairGraph 追问
        clf_repair = _entry("repair", secondary=["consult"])
        extractor = FakeFieldExtractor(
            sequence=[
                DraftExtract(description="", building=None, room="502", contact="李华"),
                DraftExtract(description="", building="3号楼", room=None, contact=None),
            ]
        )
        rg = _repair(db_session_factory, extractor=extractor)
        cg = _consult(db_session_factory)
        out1 = turn(clf_repair, rg, cg, TID, "502室灯坏了，李华")
        assert out1["route"] == "repair"
        assert out1["pending_question"]  # 报修追问挂起

        # 中间插咨询：Entry 重路由 consult → ConsultGraph 处理，RepairGraph 不 resume
        out_side = turn(_entry("consult"), rg, cg, TID, "顺便问下教务密码怎么改")
        assert out_side["route"] == "consult"
        assert out_side["outcome"] == "answer"
        assert "密码" in out_side["reply"]
        # 报修会话仍挂起（等楼栋信息），未被插话吞掉
        assert rg.get_state({"configurable": {"thread_id": TID}}).next != ()

        # 回到报修：resume 续跑建单
        out2 = turn(_entry("repair"), rg, cg, TID, "3号楼")
        assert out2["finished"] is True
        assert out2["ticket_id"] == 1


class TestPendingOtherInput:
    def test_other_reply_during_pending_resumes_repair(self, db_session_factory):
        """报修挂起时 other 类输入（如"3号楼501，李华"）→ 视为回答 resume，
        不被打到人工占位（真 LLM 评测抓出：Entry 无上下文判 other）。"""
        extractor = FakeFieldExtractor(
            sequence=[
                DraftExtract(description="", building=None, room="502", contact="李华"),
                DraftExtract(description="", building="3号楼", room=None, contact=None),
            ]
        )
        rg = _repair(db_session_factory, extractor=extractor)
        cg = _consult(db_session_factory)
        out1 = turn(_entry("repair"), rg, cg, TID, "502室灯坏了，李华")
        assert out1["pending_question"]  # 追问挂起
        out2 = turn(_entry("other", confidence=0.4), rg, cg, TID, "3号楼")  # Entry 判 other
        assert out2["route"] == "repair"  # 挂起中 other → 仍进报修
        assert out2["finished"] is True
        assert out2["ticket_id"] == 1


class TestSessionIsolation:
    def test_new_thread_new_repair(self, db_session_factory):
        """不同 thread_id = 不同报修会话（互不干扰）。"""
        rg = _repair(db_session_factory)
        cg = _consult(db_session_factory)
        out1 = turn(_entry("repair"), rg, cg, "t-a", "3号楼502灯坏了，李华")
        out2 = turn(_entry("repair"), rg, cg, "t-b", "6号楼601门锁坏了，王芳")
        assert out1["ticket_id"] == 1
        assert out2["ticket_id"] == 2

    def test_consult_thread_independent_from_repair(self, db_session_factory):
        """咨询会话与报修会话同 thread_id 不互相干扰（thread_id 是下游图各自的作用域）。"""
        rg = _repair(db_session_factory)
        cg = _consult(db_session_factory)
        out1 = turn(_entry("consult"), rg, cg, TID, "密码怎么改")
        assert out1["outcome"] == "answer"
        out2 = turn(_entry("repair"), rg, cg, TID, "3号楼502灯坏了，李华")
        assert out2["ticket_id"] == 1  # 咨询已结束后报修正常开新会话
