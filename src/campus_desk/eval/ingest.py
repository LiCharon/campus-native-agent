"""评测集入库（M3）：JSON 剧本 → DB（eval_case/eval_turn 表）。

幂等：EvalCase 按 id upsert（存在更新，不存在插入）；EvalTurn 对每个 case
先删后插（重跑不累积）。入库前跑 loader.validate_dataset 拦截坏数据。
"""

import json

from campus_desk.db.session import SessionFactory
from campus_desk.eval.db_models import EvalCase, EvalTurn
from campus_desk.eval.loader import load_all, validate_dataset
from campus_desk.eval.models import ScriptedCase


def _case_to_row(case: ScriptedCase) -> EvalCase:
    return EvalCase(
        id=case.id,
        category=case.category,
        student_input=case.student_input,
        intent=case.intent,
        expected_route=case.expected_route,
        secondary_intents=json.dumps(case.secondary_intents, ensure_ascii=False),
        note=case.note,
    )


def ingest_cases(session_factory: SessionFactory) -> tuple[int, int]:
    """JSON → DB 入库（幂等）。返回 (新增/更新 case 数, 写入 turn 数)。

    校验失败（validate_dataset 有 problem）直接抛 ValueError——入库前拦截，
    不允许脏数据进库。
    """
    cases = load_all()
    problems = validate_dataset(cases)
    if problems:
        raise ValueError("评测集校验未通过，拒绝入库：\n" + "\n".join(problems))

    case_count = 0
    turn_count = 0
    with session_factory() as session, session.begin():
        for case in cases:
            existing = session.get(EvalCase, case.id)
            row = _case_to_row(case)
            if existing is None:
                session.add(row)
            else:
                for col in (
                    "category",
                    "student_input",
                    "intent",
                    "expected_route",
                    "secondary_intents",
                    "note",
                ):
                    setattr(existing, col, getattr(row, col))
            case_count += 1
            # turns：先删后插（重跑不累积，seq 保持剧本顺序）
            session.query(EvalTurn).filter(EvalTurn.case_id == case.id).delete()
            for seq, turn in enumerate(case.turns, start=1):
                session.add(
                    EvalTurn(
                        case_id=case.id,
                        seq=seq,
                        student_reply=turn.student_reply,
                        expect=json.dumps(turn.expect, ensure_ascii=False),
                    )
                )
                turn_count += 1
    return case_count, turn_count
