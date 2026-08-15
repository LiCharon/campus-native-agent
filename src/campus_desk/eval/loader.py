"""评测集加载与校验：读 dataset/ 下全部剧本 JSON。

设计（需求 §10）：评测集独立于业务代码，JSON 文件入 git（可评审可 diff）；
M3 搭 MySQL 后做入库脚本同步，届时加载器可换成查库。
"""

import json
from pathlib import Path

from campus_desk.eval.models import ScriptedCase

DATASET_DIR = Path(__file__).parent / "dataset"

# 各类别数量区间（M1-T11 ZJUT 4 类：知识问答 15-20，其余各 2-5）
COUNT_RANGES: dict[str, tuple[int, int]] = {
    "knowledge": (15, 20),
    "tool_query": (2, 5),
    "multi_intent": (2, 5),
    "other": (2, 5),
}

# 意图标注 → 期望路由 的一致性规则（other 意图汇聚到人工，其余同意图）
_ROUTE_OF_INTENT = {
    "knowledge": "knowledge",
    "tool_query": "tool_query",
    "multi_intent": "multi_intent",
    "other": "human_handoff",
}


def load_all(dataset_dir: Path | None = None) -> list[ScriptedCase]:
    """加载 dataset/ 下全部 JSON 剧本（每文件一个数组），pydantic 校验。"""
    base = dataset_dir or DATASET_DIR
    cases: list[ScriptedCase] = []
    for path in sorted(base.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.extend(ScriptedCase.model_validate(item) for item in data)
    return cases


def validate_dataset(cases: list[ScriptedCase]) -> list[str]:
    """返回问题列表（空 = 通过）。供测试与入库前校验共用。"""
    problems: list[str] = []

    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        problems.append(f"id 重复: {sorted({i for i in ids if ids.count(i) > 1})}")

    by_category: dict[str, list[ScriptedCase]] = {}
    for case in cases:
        by_category.setdefault(case.category, []).append(case)

    for category, (lo, hi) in COUNT_RANGES.items():
        count = len(by_category.get(category, []))
        if not (lo <= count <= hi):
            problems.append(f"{category} 数量 {count} 不在区间 [{lo}, {hi}]")

    for case in cases:
        if not case.student_input.strip():
            problems.append(f"{case.id}: student_input 为空")
        if case.category == "multi_intent" and not case.secondary_intents:
            problems.append(f"{case.id}: multi_intent 剧本缺少 secondary_intents")
        if case.category != "multi_intent" and case.secondary_intents:
            problems.append(f"{case.id}: 非 multi_intent 剧本不应有 secondary_intents")
        if case.expected_route != _ROUTE_OF_INTENT[case.intent]:
            problems.append(
                f"{case.id}: 标注-路由不一致（intent={case.intent} 应路由到 "
                f"{_ROUTE_OF_INTENT[case.intent]}，实际标注 {case.expected_route}）"
            )
        if case.intent in case.secondary_intents:
            problems.append(f"{case.id}: 次要意图与主意图重复")
        for idx, turn in enumerate(case.turns, start=1):
            if not turn.student_reply.strip():
                problems.append(f"{case.id}: 第 {idx} 轮 student_reply 为空")
            for assertion in turn.expect:
                if assertion.startswith("tool:") and len(assertion) > 5:
                    continue
                if assertion.startswith("status:") and len(assertion) > 7:
                    continue
                if assertion.startswith("outcome:") and len(assertion) > 8:
                    continue
                problems.append(
                    f"{case.id}: 第 {idx} 轮非法断言 '{assertion}'"
                    "（仅支持 tool:xxx / status:xxx / outcome:xxx）"
                )
    return problems
