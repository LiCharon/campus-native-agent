"""链路评测运行器测试（M2）：全 fake 环境断言逻辑（不依赖真 LLM）。"""

from campus_desk.eval.chain_runner import (
    assert_case,
    check_keywords,
    check_tool,
)


def test_check_keywords_all_present():
    assert check_keywords(["寒假", "官方通知"], "寒假时间以官方通知为准。") == []
    assert check_keywords(["不存在"], "寒假时间") == ["关键词缺失: 不存在"]


def test_check_tool():
    assert check_tool("query_empty_rooms", ["query_empty_rooms"]) == []
    assert check_tool("query_empty_rooms", ["query_library_seats"]) == ["工具缺失: query_empty_rooms"]


def test_assert_case_knowledge_pass():
    from campus_desk.eval.models import ScriptedCase

    case = ScriptedCase(id="c-1", category="knowledge", student_input="寒假？", intent="knowledge",
                        expected_route="knowledge", expected_outcome="answer",
                        expected_entry_ids=[1], expected_keywords=["寒假"])
    result = {"route": "knowledge", "outcome": "answer", "hits": [1, 4], "reply": "寒假以通知为准。"}
    outcome = assert_case(case, result, turn_results=[])
    assert outcome.passed


def test_assert_case_knowledge_missing_entry_fails():
    from campus_desk.eval.models import ScriptedCase

    case = ScriptedCase(id="c-2", category="knowledge", student_input="寒假？", intent="knowledge",
                        expected_route="knowledge", expected_outcome="answer",
                        expected_entry_ids=[1], expected_keywords=["寒假"])
    result = {"route": "knowledge", "outcome": "answer", "hits": [9], "reply": "寒假以通知为准。"}
    assert not assert_case(case, result, turn_results=[]).passed


def test_assert_case_tool_pass():
    from campus_desk.eval.models import ScriptedCase

    case = ScriptedCase(id="c-3", category="tool_query", student_input="有空教室吗", intent="tool_query",
                        expected_route="tool_query", expected_outcome="answer",
                        expected_tool="query_empty_rooms", expected_keywords=["空闲教室"])
    result = {"route": "tool_query", "outcome": "answer",
              "tool_calls": ["query_empty_rooms"], "reply": "3号楼今天下午空闲教室：301。"}
    assert assert_case(case, result, turn_results=[]).passed
