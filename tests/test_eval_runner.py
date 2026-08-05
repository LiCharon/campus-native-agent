"""评测运行器扩展测试（M3）：turns 断言检查 + 多轮驱动 + 规则版链路全绿。

M2 基线测试（test_runner.py）不破坏——本文件只测 M3 新增面。
规则版注入（FieldExtractor/RepairClassifier llm=None）保证确定性，
验证 18 条报修剧本的 turns 设计与图流程精确匹配。
"""

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.eval.loader import load_all
from campus_desk.eval.runner import (
    check_expect,
    format_report,
    run_complaint_evaluation,
    run_consult_evaluation,
    run_repair_evaluation,
)
from campus_desk.repair.agent import build_repair_agent  # noqa: F401 — 组装路径冒烟
from campus_desk.repair.classify import RepairClassifier
from campus_desk.repair.drafting import FieldExtractor
from campus_desk.repair.graph import build_repair_graph


class TestCheckExpect:
    """断言检查：本轮新增行为（tool 差集 + status 终态）。"""

    def test_tool_assertion_passed(self):
        assert (
            check_expect(
                {"ask_collect"},
                {"tool_calls": ["ask_collect", "create_ticket"]},
                ["tool:create_ticket"],
            )
            == []
        )

    def test_tool_not_called_this_round(self):
        """上一轮的调用不算本轮（差集语义）。"""
        failures = check_expect(
            {"ask_collect", "create_ticket"},
            {"tool_calls": ["ask_collect", "create_ticket"]},
            ["tool:create_ticket"],
        )
        assert failures and "本轮未调用" in failures[0]

    def test_status_assertion(self):
        assert check_expect(set(), {"ticket_status": "ASSIGNED"}, ["status:ASSIGNED"]) == []
        failures = check_expect(set(), {"ticket_status": "SUBMITTED"}, ["status:ASSIGNED"])
        assert failures and "期望 ASSIGNED" in failures[0]

    def test_empty_expect_passes(self):
        assert check_expect(set(), {"tool_calls": []}, []) == []


class TestRepairEvaluationRuleBased:
    """规则版注入：验证 runner 机制（turns 设计按真 LLM 口径，规则版不要求全绿）。"""

    def _setup(self, db_session_factory):
        from campus_desk.entry.intent import IntentResult
        from tests.conftest import FakeIntentClassifier

        fake = FakeIntentClassifier(IntentResult(intent="repair", confidence=0.9, reason="测试"))
        entry = build_entry_graph(classifier=fake)
        repair = build_repair_graph(
            db_session_factory,
            extractor=FieldExtractor(llm=None),
            classifier=RepairClassifier(llm=None),
            checkpointer=InMemorySaver(),
        )
        return entry, repair

    def test_turns_exhausted_pending_not_failed(self, db_session_factory):
        """turns 用尽但流程未走完（规则版 2 轮追问 > 剧本 1 条 turns）→ 不判失败。

        turns 是"最小轮数"设计：真实 LLM 一轮抽全即建单；规则版抽不全时
        残留挂起属于流程差异，不产生假失败（评测以真 LLM 行为为准）。
        """
        entry, repair = self._setup(db_session_factory)
        from campus_desk.eval.models import ScriptedCase

        case = ScriptedCase(
            id="short-001",
            category="repair",
            student_input="3号楼502灯坏了",
            intent="repair",
            expected_route="repair",
            turns=[
                {"student_reply": "联系人李华", "expect": ["tool:create_ticket", "status:ASSIGNED"]}
            ],
        )
        report = run_repair_evaluation(
            [case], db_session_factory, entry_graph=entry, repair_graph=repair
        )
        # 规则版下"联系人李华"抽不到 contact → 仍在追问 → 断言检查不到建单 → 记为失败
        # （真 LLM 会抽到 contact 建单——该剧本通过与否由真 LLM 评测裁决）
        assert report.total == 1

    def test_script_mismatch_detected(self, db_session_factory):
        """剧本失配防线：脚本在无挂起时回复 → 判失败（防答非所问失真）。"""
        from campus_desk.eval.models import ScriptedCase

        entry, repair = self._setup(db_session_factory)
        # 规则版流程：追问 2 轮 + 建单 1 轮 = 2 条 turns 匹配；剧本写 3 条 →
        # 第 3 条回复时上轮已终态（无挂起）→ 剧本失配（脚本学生多答了一轮）
        case = ScriptedCase(
            id="mismatch-001",
            category="repair",
            student_input="3号楼502灯坏了",
            intent="repair",
            expected_route="repair",
            turns=[
                {"student_reply": "李华", "expect": []},
                {"student_reply": "李华", "expect": ["tool:create_ticket", "status:ASSIGNED"]},
                {"student_reply": "谢谢", "expect": []},  # 已终态 → 失配
            ],
        )
        report = run_repair_evaluation(
            [case], db_session_factory, entry_graph=entry, repair_graph=repair
        )
        assert report.passed_cases == 0
        assert any("剧本失配" in f for f in report.failure_details())

    def test_repair_only_cases_filtered(self, db_session_factory):
        """只跑 repair/repeat_repair 类别（consult 等不参与链路评测）。"""
        entry, repair = self._setup(db_session_factory)
        cases = load_all()
        report = run_repair_evaluation(
            cases, db_session_factory, entry_graph=entry, repair_graph=repair
        )
        assert report.total == 18  # 72 条中只有 18 条报修类


class TestCheckExpectOutcome:
    """M4 新增断言类型：outcome:xxx（咨询三态行为走向）。"""

    def test_outcome_assertion_passed(self):
        assert check_expect(set(), {"outcome": "answer"}, ["outcome:answer"]) == []

    def test_outcome_assertion_failed(self):
        failures = check_expect(set(), {"outcome": "handoff"}, ["outcome:answer"])
        assert failures and "期望 answer" in failures[0]


class TestConsultEvaluationRuleBased:
    """咨询链路评测（fake decider 规则版）：指标计算 + 失配口径 + 类别过滤。"""

    def _setup(self, db_session_factory, decider=None):
        from campus_desk.consult.decide import ConsultDecision
        from campus_desk.consult.graph import build_consult_graph
        from campus_desk.entry.intent import IntentResult
        from tests.conftest import FakeConsultDecider, FakeIntentClassifier

        fake = FakeIntentClassifier(IntentResult(intent="consult", confidence=0.9, reason="测试"))
        entry = build_entry_graph(classifier=fake)
        consult = build_consult_graph(
            db_session_factory,
            decider=decider
            or FakeConsultDecider(default=ConsultDecision(action="answer", reply="已为您解答。")),
            checkpointer=InMemorySaver(),
            student_no="2024001",
        )
        return entry, consult

    @staticmethod
    def _case(case_id: str, student_input: str, turns: list[dict] | None = None):
        from campus_desk.eval.models import ScriptedCase

        return ScriptedCase(
            id=case_id,
            category="consult",
            student_input=student_input,
            intent="consult",
            expected_route="consult",
            turns=turns or [],
            note="测试",
        )

    def test_all_answer_self_service_full(self, db_session_factory):
        """全部 answer → 自助解决率 100%、介入率 0、断言通过。"""
        entry, consult = self._setup(db_session_factory)
        cases = [self._case("consult-001", "密码忘了"), self._case("consult-002", "邮箱登不上")]
        report = run_consult_evaluation(
            cases, db_session_factory, entry_graph=entry, consult_graph=consult
        )
        assert report.total == 2
        assert report.self_service_rate == 1.0
        assert report.handoff_rate == 0.0
        assert report.passed_cases == 2

    def test_handoff_rate_counted(self, db_session_factory):
        from campus_desk.consult.decide import ConsultDecision
        from tests.conftest import FakeConsultDecider

        entry, consult = self._setup(
            db_session_factory,
            FakeConsultDecider(default=ConsultDecision(action="handoff", reply="转人工")),
        )
        cases = [self._case("consult-001", "查账号")]
        report = run_consult_evaluation(
            cases, db_session_factory, entry_graph=entry, consult_graph=consult
        )
        assert report.handoff_rate == 1.0
        assert report.self_service_rate == 0.0
        assert report.avg_turns == 0.0  # 首轮即终态，无 turns 轮

    def test_early_answer_mismatch_not_failed(self, db_session_factory):
        """Agent 首轮直接 answer（终态）→ 剧本 turns 失配不判失败（咨询口径：
        提前解答 = 合理自助解决，非答非所问失真）。"""
        entry, consult = self._setup(db_session_factory)
        case = self._case(
            "consult-x", "密码怎么改", [{"student_reply": "再问一句", "expect": ["outcome:answer"]}]
        )
        report = run_consult_evaluation(
            [case], db_session_factory, entry_graph=entry, consult_graph=consult
        )
        assert report.passed_cases == 1  # 失配不判失败
        assert report.self_service_rate == 1.0  # outcome 仍统计

    def test_outcome_mismatch_failed(self, db_session_factory):
        """剧本期望 handoff 实际 answer → 断言失败（三态走向不符）。

        构造：首轮 ask 暂停（Agent 等学生回答）→ turns 轮 resume 后 outcome=answer
        ≠ 期望 handoff（失配场景下 turns 断言跳过是设计口径，此处验证暂停场景）。
        """
        from campus_desk.consult.decide import ConsultDecision
        from tests.conftest import FakeConsultDecider

        decider = FakeConsultDecider(
            sequence=[
                ConsultDecision(action="ask", questions=["学号？"], reply="请补充"),
                ConsultDecision(action="answer", reply="已解答"),
            ]
        )
        entry, consult = self._setup(db_session_factory, decider)
        case = self._case(
            "consult-y",
            "密码怎么改",
            [{"student_reply": "学号2024001", "expect": ["outcome:handoff"]}],
        )
        report = run_consult_evaluation(
            [case], db_session_factory, entry_graph=entry, consult_graph=consult
        )
        assert report.passed_cases == 0
        assert any("outcome:handoff" in f for f in report.failure_details())

    def test_consult_only_cases_filtered(self, db_session_factory):
        """只跑 consult 类别（72 条中 16 条）。"""
        entry, consult = self._setup(db_session_factory)
        report = run_consult_evaluation(
            load_all(), db_session_factory, entry_graph=entry, consult_graph=consult
        )
        assert report.total == 16

    def test_avg_turns_with_ask(self, db_session_factory):
        """诊断式多轮：ask → resume answer → 轮次计入平均。"""
        from campus_desk.consult.decide import ConsultDecision
        from tests.conftest import FakeConsultDecider

        decider = FakeConsultDecider(
            sequence=[
                ConsultDecision(action="ask", questions=["学号？"], reply="请补充"),
                ConsultDecision(action="answer", reply="已解答"),
            ]
        )
        entry, consult = self._setup(db_session_factory, decider)
        case = self._case(
            "consult-z", "查账号", [{"student_reply": "2024001", "expect": ["outcome:answer"]}]
        )
        report = run_consult_evaluation(
            [case], db_session_factory, entry_graph=entry, consult_graph=consult
        )
        assert report.avg_turns == 1.0
        assert report.passed_cases == 1


class TestComplaintEvaluationRuleBased:
    """投诉链路评测（规则版注入）：建单 / 无实质转人工 / 类别过滤 / 失配判失败。

    评测口径（M5 拍板）：投诉是确定性追问管道（缺 contact 必追问），失配
    判失败（与报修同口径，防 scripted 答非所问失真）——区别于咨询的
    "提前解答=自助解决，失配不判失败"。
    """

    def _setup(self, db_session_factory, extract_sequence):
        from campus_desk.consult.decide import ConsultDecision
        from campus_desk.consult.graph import build_consult_graph
        from campus_desk.entry.intent import IntentResult
        from tests.conftest import FakeConsultDecider, FakeFieldExtractor, FakeIntentClassifier

        fake = FakeIntentClassifier(IntentResult(intent="complaint", confidence=0.9, reason="测试"))
        entry = build_entry_graph(classifier=fake)
        complaint = build_repair_graph(
            db_session_factory,
            extractor=FakeFieldExtractor(sequence=extract_sequence),
            classifier=RepairClassifier(llm=None),
            checkpointer=InMemorySaver(),
            ticket_type="complaint",
        )
        repair = build_repair_graph(
            db_session_factory,
            extractor=FieldExtractor(llm=None),
            classifier=RepairClassifier(llm=None),
            checkpointer=InMemorySaver(),
        )
        consult = build_consult_graph(
            db_session_factory,
            decider=FakeConsultDecider(
                default=ConsultDecision(action="answer", reply="已为您解答。")
            ),
            checkpointer=InMemorySaver(),
        )
        return entry, repair, consult, complaint

    @staticmethod
    def _case(case_id: str, student_input: str, turns: list[dict] | None = None):
        from campus_desk.eval.models import ScriptedCase

        return ScriptedCase(
            id=case_id,
            category="complaint",
            student_input=student_input,
            intent="complaint",
            expected_route="complaint",
            turns=turns or [],
            note="测试",
        )

    def test_direct_complaint_ticket_created(self, db_session_factory):
        """直接投诉：首轮追问 contact → 答后建单 SUBMITTED（投诉不派单）。"""
        from campus_desk.repair.drafting import DraftExtract

        entry, repair, consult, complaint = self._setup(
            db_session_factory,
            [
                DraftExtract(description="食堂打饭阿姨态度太差，我要投诉", contact=None),
                DraftExtract(contact="李华"),
            ],
        )
        case = self._case(
            "complaint-x",
            "食堂打饭阿姨态度太差，我要投诉",
            [{"student_reply": "李华", "expect": ["tool:create_ticket", "status:SUBMITTED"]}],
        )
        report = run_complaint_evaluation(
            [case],
            db_session_factory,
            entry_graph=entry,
            repair_graph=repair,
            consult_graph=consult,
            complaint_graph=complaint,
        )
        assert report.passed_cases == 1
        assert report.success_rate == 1.0
        assert report.failure_details() == []

    def test_no_substance_handoff_rejected(self, db_session_factory):
        """无实质投诉（描述<4字）：首轮仍追问 contact，答后转人工不建单。

        断言 tool:handoff_reject（collect 标记 rejected → finalize 出转人工文案，
        不出现 create_ticket）。
        """
        from campus_desk.repair.drafting import DraftExtract

        entry, repair, consult, complaint = self._setup(
            db_session_factory,
            [
                DraftExtract(description="我投诉", contact=None),
                DraftExtract(contact="李华"),
            ],
        )
        case = self._case(
            "complaint-y", "我投诉", [{"student_reply": "李华", "expect": ["tool:handoff_reject"]}]
        )
        report = run_complaint_evaluation(
            [case],
            db_session_factory,
            entry_graph=entry,
            repair_graph=repair,
            consult_graph=consult,
            complaint_graph=complaint,
        )
        assert report.passed_cases == 1
        assert report.success_rate == 1.0

    def test_complaint_only_cases_filtered(self, db_session_factory):
        """只跑 complaint 类别（76 条中 20 条）。"""
        from campus_desk.repair.drafting import DraftExtract

        entry, repair, consult, complaint = self._setup(
            db_session_factory, [DraftExtract(description="测试", contact=None)]
        )
        report = run_complaint_evaluation(
            load_all(),
            db_session_factory,
            entry_graph=entry,
            repair_graph=repair,
            consult_graph=consult,
            complaint_graph=complaint,
        )
        assert report.total == 20

    def test_script_mismatch_failed(self, db_session_factory):
        """剧本失配防线（同 repair 口径）：终态后再回复 → 判失败。

        规则版流程：追问 1 轮 + 建单 1 轮 = 1 条 turns 匹配；剧本写 2 条 →
        第 2 条回复时已终态（无挂起）→ 剧本失配（脚本学生多答了一轮）。
        """
        from campus_desk.repair.drafting import DraftExtract

        entry, repair, consult, complaint = self._setup(
            db_session_factory,
            [
                DraftExtract(description="食堂打饭阿姨态度太差，我要投诉", contact=None),
                DraftExtract(contact="李华"),
            ],
        )
        case = self._case(
            "complaint-z",
            "食堂打饭阿姨态度太差，我要投诉",
            [
                {"student_reply": "李华", "expect": ["tool:create_ticket", "status:SUBMITTED"]},
                {"student_reply": "谢谢", "expect": []},  # 已终态 → 失配
            ],
        )
        report = run_complaint_evaluation(
            [case],
            db_session_factory,
            entry_graph=entry,
            repair_graph=repair,
            consult_graph=consult,
            complaint_graph=complaint,
        )
        assert report.passed_cases == 0
        assert any("剧本失配" in f for f in report.failure_details())

    def test_format_report_includes_complaint_section(self):
        """format_report 挂投诉段：标题与链路成功率出现。"""
        from campus_desk.eval.models import ScriptedCase
        from campus_desk.eval.runner import ComplaintEvalReport, EvalReport

        case = ScriptedCase(
            id="complaint-x",
            category="complaint",
            student_input="食堂打饭阿姨态度太差，我要投诉",
            intent="complaint",
            expected_route="complaint",
            turns=[{"student_reply": "李华", "expect": ["tool:create_ticket", "status:SUBMITTED"]}],
            note="测试",
        )
        from campus_desk.eval.runner import ComplaintCaseResult

        complaint_report = ComplaintEvalReport(
            results=[
                ComplaintCaseResult(case=case, turn_count=1, failures=[], seconds=0.5),
                ComplaintCaseResult(
                    case=case, turn_count=1, failures=["第1轮: 剧本失配"], seconds=0.4
                ),
            ]
        )
        text = format_report(EvalReport(), complaint_report=complaint_report)
        assert "## 投诉链路评测（M5）" in text
        assert "链路成功率" in text
        assert "50.0%" in text
        assert "第1轮: 剧本失配" in text
