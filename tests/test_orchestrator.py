"""编排层测试（M3）：每轮先 Entry 后 Repair；resume 判定；多意图不吞报修。

注入：FakeIntentClassifier（Entry）+ FakeFieldExtractor/FakeRepairClassifier
（RepairGraph）+ InMemorySaver。
"""

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.intent import IntentResult
from campus_desk.entry.orchestrator import turn
from campus_desk.repair.classify import ClassificationResult
from campus_desk.repair.drafting import DraftExtract
from campus_desk.repair.graph import build_repair_graph
from tests.conftest import FakeFieldExtractor, FakeIntentClassifier, FakeRepairClassifier

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


class TestRepairFlow:
    def test_first_turn_creates_ticket(self, db_session_factory):
        """报修首轮：Entry 分流 → RepairGraph 建单派单。"""
        out = turn(_entry("repair"), _repair(db_session_factory), TID, "3号楼502灯坏了，李华")
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
        out1 = turn(_entry("repair"), rg, TID, "502室灯坏了，李华")
        assert out1["pending_question"] and "楼栋" in out1["pending_question"]
        out2 = turn(_entry("repair"), rg, TID, "3号楼")
        assert out2["finished"] is True
        assert out2["ticket_id"] == 1


class TestNonRepairRoutes:
    def test_consult_placeholder(self, db_session_factory):
        """咨询路由 → M2 占位回复（M4 接 ConsultAgent）。"""
        out = turn(_entry("consult"), _repair(db_session_factory), TID, "密码怎么改")
        assert out["route"] == "consult"
        assert "咨询" in out["reply"]
        assert out.get("ticket_id") is None  # 不触发 RepairGraph

    def test_handoff_placeholder(self, db_session_factory):
        out = turn(_entry("other", confidence=0.4), _repair(db_session_factory), TID, "你好")
        assert out["route"] == "human_handoff"
        assert "人工" in out["reply"]


class TestMultiIntentNotSwallowed:
    def test_side_question_during_repair_goes_to_consult(self, db_session_factory):
        """报修中插咨询（"顺便问密码"）：Entry 每轮重路由 → 咨询占位，
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
        out1 = turn(clf_repair, rg, TID, "502室灯坏了，李华")
        assert out1["route"] == "repair"
        assert out1["pending_question"]  # 报修追问挂起

        # 中间插咨询：Entry 重路由 consult → 占位回复，RepairGraph 不 resume
        out_side = turn(_entry("consult"), rg, TID, "顺便问下教务密码怎么改")
        assert out_side["route"] == "consult"
        assert "咨询" in out_side["reply"]
        # 报修会话仍挂起（等楼栋信息），未被插话吞掉
        assert rg.get_state({"configurable": {"thread_id": TID}}).next != ()

        # 回到报修：resume 续跑建单
        out2 = turn(_entry("repair"), rg, TID, "3号楼")
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
        out1 = turn(_entry("repair"), rg, TID, "502室灯坏了，李华")
        assert out1["pending_question"]  # 追问挂起
        out2 = turn(_entry("other", confidence=0.4), rg, TID, "3号楼")  # Entry 判 other
        assert out2["route"] == "repair"  # 挂起中 other → 仍进报修
        assert out2["finished"] is True
        assert out2["ticket_id"] == 1


class TestSessionIsolation:
    def test_new_thread_new_repair(self, db_session_factory):
        """不同 thread_id = 不同报修会话（互不干扰）。"""
        rg = _repair(db_session_factory)
        out1 = turn(_entry("repair"), rg, "t-a", "3号楼502灯坏了，李华")
        out2 = turn(_entry("repair"), rg, "t-b", "6号楼601门锁坏了，王芳")
        assert out1["ticket_id"] == 1
        assert out2["ticket_id"] == 2
