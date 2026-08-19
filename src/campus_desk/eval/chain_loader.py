"""链路评测集加载与校验（M2）：dataset/chain/ 下全部 JSON。

设计（拍板）：独立子目录——loader.load_all 的 glob("*.json") 不递归，
意图剧本（dataset/*.json）与链路剧本天然隔离，互不污染。
"""

import json
from pathlib import Path

from campus_desk.eval.loader import route_of_intent, valid_assertion
from campus_desk.eval.models import ScriptedCase

CHAIN_DATASET_DIR = Path(__file__).parent / "dataset" / "chain"


def load_chain_cases(dataset_dir: Path | None = None) -> list[ScriptedCase]:
    base = dataset_dir or CHAIN_DATASET_DIR
    cases: list[ScriptedCase] = []
    for path in sorted(base.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.extend(ScriptedCase.model_validate(item) for item in data)
    return cases


def validate_chain_dataset(cases: list[ScriptedCase]) -> list[str]:
    """返回问题列表（空 = 通过）。字段约束比意图集更强（答案正确性口径）。"""
    problems: list[str] = []

    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        problems.append("id 重复")

    for case in cases:
        if not case.student_input.strip():
            problems.append(f"{case.id}: student_input 为空")
        if case.expected_route != route_of_intent(case.intent):
            problems.append(f"{case.id}: 标注-路由不一致")
        if (
            case.category == "knowledge"
            and not case.expected_entry_ids
            and case.expected_outcome != "handoff"
        ):
            # 转人工链路剧本（expected_outcome=handoff）不标注条目 id
            problems.append(f"{case.id}: knowledge 剧本缺 expected_entry_ids")
        if case.category == "tool_query" and not case.expected_tool:
            problems.append(f"{case.id}: tool_query 剧本缺 expected_tool")
        if case.inject_error and case.expected_outcome != "degraded":
            problems.append(f"{case.id}: inject_error 剧本 expected_outcome 应为 degraded")
        if case.expected_outcome == "degraded" and case.category == "tool_query" and not case.inject_error:
            problems.append(f"{case.id}: degraded 剧本缺 inject_error（注入机制未启用）")
        if not case.expected_keywords:
            problems.append(f"{case.id}: 缺 expected_keywords（答案正确性口径必填）")
        if case.category == "multi_intent" and not case.secondary_intents:
            problems.append(f"{case.id}: multi_intent 剧本缺 secondary_intents")
        if case.intent in case.secondary_intents:
            problems.append(f"{case.id}: 次要意图与主意图重复")
        for idx, turn in enumerate(case.turns, start=1):
            if not turn.student_reply.strip():
                problems.append(f"{case.id}: 第 {idx} 轮 student_reply 为空")
            for assertion in turn.expect:
                if not valid_assertion(assertion):
                    problems.append(f"{case.id}: 第 {idx} 轮非法断言 '{assertion}'")
    return problems
