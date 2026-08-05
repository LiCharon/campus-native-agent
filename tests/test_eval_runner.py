"""评测运行器扩展测试（M3）：turns 断言检查 + 多轮驱动 + 规则版链路全绿。

M2 基线测试（test_runner.py）不破坏——本文件只测 M3 新增面。
规则版注入（FieldExtractor/RepairClassifier llm=None）保证确定性，
验证 18 条报修剧本的 turns 设计与图流程精确匹配。
"""

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.eval.loader import load_all
from campus_desk.eval.runner import check_expect, run_repair_evaluation
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
    """规则版注入跑 18 条报修剧本：turns 设计与图流程精确匹配（确定性验证）。"""

    def _setup(self, db_session_factory):
        entry = build_entry_graph()  # 真 LLM 意图分类：剧本标注驱动（首轮走 Entry）
        from campus_desk.entry.intent import IntentResult
        from tests.conftest import FakeIntentClassifier

        # 意图分类也用规则版确定性注入（避免 LLM 意图抖动干扰链路断言）
        fake = FakeIntentClassifier(IntentResult(intent="repair", confidence=0.9, reason="测试"))
        entry = build_entry_graph(classifier=fake)
        repair = build_repair_graph(
            db_session_factory,
            extractor=FieldExtractor(llm=None),
            classifier=RepairClassifier(llm=None),
            checkpointer=InMemorySaver(),
        )
        return entry, repair

    def test_all_repair_cases_pass(self, db_session_factory):
        """规则版下 18 条报修剧本 turns 断言全过（链路成功率 100%）。"""
        entry, repair = self._setup(db_session_factory)
        cases = [c for c in load_all() if c.category in ("repair", "repeat_repair")]
        report = run_repair_evaluation(
            cases, db_session_factory, entry_graph=entry, repair_graph=repair
        )
        assert report.total == 18
        assert report.passed_cases == 18, report.failure_details()
        assert report.avg_turns >= 2  # 规则版每条至少 2 轮（追问+建单）

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
