"""链路评测运行器（M2）：逐剧本驱动 orchestrator.turn 多轮，断言行为+答案正确性。

口径（拍板 Q9/§9）：
- 知识剧本：期望条目 id ⊆ 实际命中 hits（允许多命中）+ 关键词全部出现在最终回复
- 工具剧本：tool_calls 含期望工具名 + 关键词全出现；追问剧本 turns 逐轮断言
- multi 剧本：route=multi_intent + 关键词（含"可以继续问我"提示）
- 挂起恢复由 orchestrator 内部处理（thread_id 派生隔离两图）

环境：SQLite 内存库（种子+mock）+ InMemorySaver；真 LLM 跑分，无 key SKIP 不进 CI。

用法：python -m campus_desk.eval.chain_runner [--max N] [--out path]
"""

import argparse
import time
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from campus_desk import telemetry
from campus_desk.config import settings
from campus_desk.db.base import Base
from campus_desk.db.seed import seed_all
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn
from campus_desk.eval import db_models  # noqa: F401 — 注册 eval 表进 Base.metadata
from campus_desk.eval.chain_loader import load_chain_cases
from campus_desk.eval.models import ScriptedCase
from campus_desk.knowledge.graph import build_knowledge_graph
from campus_desk.query.graph import build_query_graph, lookup_student_no

# M2+：链路评测固定"今天"（2026-2027-1 第 6 周周四）→ 时间上下文确定性
#（"这周/这学期/上学期"换算稳定，不随真实日期漂移）
TODAY_FIXED = date(2026, 10, 15)


class _BrokenFactory:
    """inject_error=db：任何会话抛 RuntimeError（模拟数据源宕机，走四层失败链）。"""

    def __init__(self, real):
        self.real = real

    def __call__(self):
        raise RuntimeError("db down")


@dataclass
class CaseOutcome:
    case: ScriptedCase
    passed: bool
    problems: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _make_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_all(factory)
    return factory


def check_keywords(keywords: list[str], reply: str) -> list[str]:
    return [f"关键词缺失: {k}" for k in keywords if k not in reply]


def check_tool(expected: str, tool_calls: list[str]) -> list[str]:
    return [] if expected in tool_calls else [f"工具缺失: {expected}"]


def assert_case(
    case: ScriptedCase, first: dict, final: dict, turn_results: list[dict]
) -> CaseOutcome:
    """断言：route/expected_outcome 用首轮结果；条目/工具/关键词用最终轮结果。"""
    problems: list[str] = []
    if first.get("route") != case.expected_route:
        problems.append(f"route: 期望 {case.expected_route} 实际 {first.get('route')}")
    if case.expected_outcome and first.get("outcome") != case.expected_outcome:
        problems.append(f"outcome: 期望 {case.expected_outcome} 实际 {first.get('outcome')}")
    if case.category == "knowledge" and case.expected_entry_ids:
        missing = [i for i in case.expected_entry_ids if i not in final.get("hits", [])]
        if missing:
            problems.append(f"条目未命中: {missing}（实际 hits={final.get('hits')}）")
    if case.category == "tool_query" and case.expected_tool:
        problems.extend(check_tool(case.expected_tool, final.get("tool_calls", [])))
    problems.extend(check_keywords(case.expected_keywords, final.get("reply", "")))
    for idx, t in enumerate(case.turns):
        r = turn_results[idx] if idx < len(turn_results) else {}
        for assertion in t.expect:
            if assertion.startswith("outcome:") and r.get("outcome") != assertion[8:]:
                problems.append(f"第 {idx + 1} 轮 {assertion} 实际 {r.get('outcome')}")
            if assertion.startswith("tool:") and assertion[5:] not in r.get("tool_calls", []):
                problems.append(f"第 {idx + 1} 轮 {assertion} 实际 {r.get('tool_calls')}")
    return CaseOutcome(case=case, passed=not problems, problems=problems)


def run_chain_evaluation(
    cases=None, *, classifier=None, decider=None, tool_llm=None, max_cases=None, today=None
):
    """跑一遍链路评测。classifier/decider/tool_llm 可注入（测试用 fake），默认真 LLM。

    M2+ inject_error：建 query_ok / query_broken 双图，按剧本 inject_error 字段选择——
    degraded 剧本走 _BrokenFactory（数据源宕机），断言 outcome=degraded 走四层失败链。
    """
    cases = cases or load_chain_cases()
    if max_cases:
        cases = cases[:max_cases]
    factory = _make_factory()
    from langgraph.checkpoint.memory import InMemorySaver

    fixed_today = today or TODAY_FIXED
    entry = build_entry_graph(classifier=classifier)
    knowledge = build_knowledge_graph(factory, checkpointer=InMemorySaver(), decider=decider)
    student_no = lookup_student_no(factory, "student-001")
    query_ok = build_query_graph(
        factory,
        checkpointer=InMemorySaver(),
        llm=tool_llm,
        student_no=student_no,
        today=fixed_today,
    )
    query_broken = build_query_graph(
        _BrokenFactory(factory),
        checkpointer=InMemorySaver(),
        llm=tool_llm,
        student_no=student_no,
        today=fixed_today,
    )

    outcomes: list[CaseOutcome] = []
    start = time.monotonic()
    for case in cases:
        t0 = time.monotonic()
        thread_id = f"chain-{case.id}"
        query = query_broken if case.inject_error else query_ok
        first = turn(entry, knowledge, query, thread_id, case.student_input)
        turn_results = [
            turn(entry, knowledge, query, thread_id, t.student_reply) for t in case.turns
        ]
        final = turn_results[-1] if turn_results else first
        outcome = assert_case(case, first, final, turn_results)
        outcome.seconds = time.monotonic() - t0
        outcomes.append(outcome)
    return outcomes, time.monotonic() - start


def format_chain_report(outcomes: list[CaseOutcome], duration: float) -> str:
    total = len(outcomes)
    passed = sum(1 for o in outcomes if o.passed)
    lines = [
        "# Campus Native Agent 链路评测报告（M2）",
        "",
        f"- 剧本数: {total}",
        (f"- 通过率: **{passed / total:.1%}**（{passed}/{total}）" if total else "- 通过率: -"),
        f"- 总耗时: {duration:.1f}s",
        "",
        "## 失败用例明细",
        "",
    ]
    fails = [o for o in outcomes if not o.passed]
    if not fails:
        lines.append("无（全部通过）")
    for o in fails:
        lines.append(f"- {o.case.id}（{o.case.category}）: {'；'.join(o.problems)}")
    lines += ["", "## 全部用例", ""]
    for o in outcomes:
        mark = "✅" if o.passed else "❌"
        lines.append(f"- {mark} {o.case.id}（{o.case.category}）: {o.case.student_input}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Campus Native Agent 链路评测（M2）")
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args()

    if not settings.deepseek_api_key:
        print("SKIP: 未配置 DEEPSEEK_API_KEY（.env 填写后重跑）——需外部环境的项不进 CI")
        return

    outcomes, duration = run_chain_evaluation(max_cases=args.max)
    text = format_chain_report(outcomes, duration)
    telemetry.flush()
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(text, encoding="utf-8")
        print(f"报告已写入: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
