"""评测集入库测试（M3）：JSON → SQLite（幂等 + 校验拦截）。"""

from campus_desk.eval.db_models import EvalCase, EvalTurn
from campus_desk.eval.ingest import ingest_cases
from campus_desk.eval.loader import load_all, validate_dataset
from campus_desk.eval.models import ScriptedCase


class TestIngest:
    def test_ingest_all_cases(self, db_session_factory):
        """72 条全部入库 + turns 落库。"""
        cases, turns = ingest_cases(db_session_factory)
        assert cases == 72
        assert turns > 0  # repair/repeat_repair 剧本有 turns
        with db_session_factory() as session, session.begin():
            assert session.query(EvalCase).count() == 72
            repair = session.query(EvalCase).filter(EvalCase.id == "repair-001").first()
            assert repair.student_input.startswith("宿舍的灯坏了")
            turn_rows = (
                session.query(EvalTurn)
                .filter(EvalTurn.case_id == "repair-001")
                .order_by(EvalTurn.seq)
                .all()
            )
            assert len(turn_rows) == 2
            assert turn_rows[1].expect == '["tool:create_ticket", "status:ASSIGNED"]'

    def test_ingest_idempotent(self, db_session_factory):
        """重入库不累积（EvalCase upsert + EvalTurn 先删后插）。"""
        ingest_cases(db_session_factory)
        cases, turns = ingest_cases(db_session_factory)
        assert cases == 72
        with db_session_factory() as session, session.begin():
            assert session.query(EvalCase).count() == 72
            total_turns = session.query(EvalTurn).count()
        assert total_turns == turns  # 重跑后 turn 数与本次写入一致（无累积）

    def test_ingest_rejects_invalid_dataset(self, db_session_factory):
        """校验不过拒绝入库：非法断言前缀被 loader 拦截（ingest 前置校验）。"""
        bad = ScriptedCase(
            id="bad-001",
            category="repair",
            student_input="灯坏了",
            intent="repair",
            expected_route="repair",
            turns=[
                {"student_reply": "3号楼502", "expect": ["magic:create_ticket"]}  # 非法前缀
            ],
        )
        problems = validate_dataset([bad])
        assert any("非法断言" in p for p in problems)

    def test_real_dataset_passes_validation(self, db_session_factory):
        """磁盘数据集 turns 断言全部合法（含本轮 turns 后，入库前置校验通过）。"""
        cases = load_all()
        assert validate_dataset(cases) == []
        ingest_cases(db_session_factory)  # 不抛 = 校验通过
