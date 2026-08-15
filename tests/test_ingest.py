"""评测集入库测试（M3）：JSON → SQLite（幂等 + 校验拦截）。"""

from campus_desk.eval.db_models import EvalCase, EvalTurn
from campus_desk.eval.ingest import ingest_cases
from campus_desk.eval.loader import load_all, validate_dataset
from campus_desk.eval.models import ScriptedCase


class TestIngest:
    def test_ingest_all_cases(self, db_session_factory):
        """24 条全部入库（M1-T11 ZJUT 意图集，M1 只有入口分流故 turns 为空）。"""
        cases, turns = ingest_cases(db_session_factory)
        assert cases == 24
        assert turns == 0  # M1 剧本 turns 留空（入口分流评测）
        with db_session_factory() as session, session.begin():
            assert session.query(EvalCase).count() == 24
            first = session.query(EvalCase).filter(EvalCase.id == "zjut-intent-001").first()
            assert first.student_input == "什么时候放寒假？"
            assert first.expected_route == "knowledge"
            assert session.query(EvalTurn).count() == 0

    def test_ingest_idempotent(self, db_session_factory):
        """重入库不累积（EvalCase upsert + EvalTurn 先删后插）。"""
        ingest_cases(db_session_factory)
        cases, turns = ingest_cases(db_session_factory)
        assert cases == 24
        with db_session_factory() as session, session.begin():
            assert session.query(EvalCase).count() == 24
            total_turns = session.query(EvalTurn).count()
        assert total_turns == turns  # 重跑后 turn 数与本次写入一致（无累积）

    def test_ingest_rejects_invalid_dataset(self, db_session_factory):
        """校验不过拒绝入库：非法断言前缀被 loader 拦截（ingest 前置校验）。"""
        bad = ScriptedCase(
            id="bad-001",
            category="knowledge",
            student_input="怎么补办学生证？",
            intent="knowledge",
            expected_route="knowledge",
            turns=[
                {"student_reply": "好的", "expect": ["magic:create_ticket"]}  # 非法前缀
            ],
        )
        problems = validate_dataset([bad])
        assert any("非法断言" in p for p in problems)

    def test_real_dataset_passes_validation(self, db_session_factory):
        """磁盘数据集 turns 断言全部合法（含本轮 turns 后，入库前置校验通过）。"""
        cases = load_all()
        assert validate_dataset(cases) == []
        ingest_cases(db_session_factory)  # 不抛 = 校验通过
