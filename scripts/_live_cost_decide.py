"""M13 真实链路实测（补 decide 调用点）：发若干知识库里大概率 miss 的问题，
触发 knowledge 分支的 ClarifyDecider.decide（追问/转人工决策），让 decide 调用点真实落库。
沿用 _live_cost_demo.py 的 thread，数据累积到同一会话。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func

from campus_desk.db.models import LLMUsage
from campus_desk.db.session import default_session_factory
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn
from campus_desk.knowledge.graph import build_knowledge_graph
from campus_desk.query.graph import build_query_graph, lookup_student_no

# 知识库里大概率没有准确答案 → 走 missed 分支 → 触发 decide
MISS_PRONE = [
    "我想在校园里申请无人机飞行许可怎么弄",
    "食堂能租个小摊位卖我自己做的手作吗",
    "学校有没有教学生开拖拉机的课",
]

USER_ID = "student-001"
THREAD = "live-cost-demo-20260829"


def main() -> None:
    factory = default_session_factory()
    entry = build_entry_graph()
    knowledge = build_knowledge_graph(factory, checkpointer=InMemorySaver(), decider=None)
    student_no = lookup_student_no(factory, USER_ID)
    query = build_query_graph(
        factory,
        checkpointer=InMemorySaver(),
        llm=None,
        student_no=student_no,
        today=datetime.now(UTC).date(),
    )
    for i, msg in enumerate(MISS_PRONE, 1):
        print(f"── [decide {i}/{len(MISS_PRONE)}] {msg}")
        try:
            res = turn(entry, knowledge, query, THREAD, msg, user_id=USER_ID)
            reply = (res.get("reply") or "").replace("\n", " ")
            print(
                f"   route={res.get('route')!r} outcome={res.get('outcome')!r} reply={reply[:48]!r}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"   ⚠ turn 异常：{type(exc).__name__}: {exc}")

    # 查本 thread 的调用点分布
    with factory() as s, s.begin():
        rows = (
            s.query(LLMUsage.call_point, func.count(LLMUsage.id))
            .filter(LLMUsage.thread_id == THREAD)
            .group_by(LLMUsage.call_point)
            .all()
        )
    print(f"\n=== thread={THREAD} 调用点分布 ===")
    for cp, n in rows:
        print(f"  {cp:<12} {n}")


if __name__ == "__main__":
    raise SystemExit(main())
