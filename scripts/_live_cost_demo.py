"""M13 真实链路实测：用生产级三图（与 eval/chain_runner 同款构造）+ 真实 DeepSeek，
驱动若干条真实消息，让 usage 埋点落库到生产 llm_usage，随后由 cost_report 出真实数字。

不清理数据——这是"真实测一下"，要看到真库里的行。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.db.models import LLMUsage
from campus_desk.db.session import default_session_factory
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn
from campus_desk.knowledge.graph import build_knowledge_graph
from campus_desk.query.graph import build_query_graph, lookup_student_no

# 真实对话样本（覆盖三类路由，触发不同 LLM 调用点）
TURNS = [
    ("图书馆今天几点关门？", "knowledge → intent（+可能 decide）"),
    ("查询一下我的绩点是多少", "tool_query → intent + tool_select"),
    ("明天哪间教室有空？帮我查", "tool_query → intent + tool_select"),
    ("图书馆几点关门，另外看下我的课表", "multi_intent → intent + 分支"),
    ("你们学校奖学金怎么申请", "knowledge → intent（+可能 decide）"),
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
    print(f"student_no={student_no!r}  thread={THREAD}\n")

    for i, (msg, expect) in enumerate(TURNS, 1):
        print(f"── [{i}/{len(TURNS)}] {msg}  （预期 {expect}）")
        try:
            res = turn(entry, knowledge, query, THREAD, msg, user_id=USER_ID)
            reply = (res.get("reply") or "").replace("\n", " ")
            print(f"   route={res.get('route')!r}  reply={reply[:56]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"   ⚠ turn 抛异常（但不影响已落库的 intent 等）：{type(exc).__name__}: {exc}")

    # 落库实况
    with factory() as s, s.begin():
        rows = (
            s.query(
                LLMUsage.call_point,
                LLMUsage.route,
                LLMUsage.model,
                LLMUsage.prompt_tokens,
                LLMUsage.completion_tokens,
                LLMUsage.status,
            )
            .filter(LLMUsage.thread_id == THREAD)
            .order_by(LLMUsage.id)
            .all()
        )
    print(f"\n=== 本轮落入 llm_usage 共 {len(rows)} 行（thread={THREAD}）===")
    for r in rows:
        print(
            f"  call_point={r.call_point:<11} route={r.route!s:<12} "
            f"model={r.model:<28} in={r.prompt_tokens:>5} out={r.completion_tokens:>4} "
            f"status={r.status}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
