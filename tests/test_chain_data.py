"""链路数据集校验测试（M2）：dataset/chain/ 独立目录 + 类别字段约束。"""

from campus_desk.eval.chain_loader import load_chain_cases, validate_chain_dataset


def test_load_chain_cases_reads_chain_dir_only():
    cases = load_chain_cases()
    assert 40 <= len(cases) <= 50  # M2+ FC 扩展：12 工具全覆盖（直查/追问/降级三类）
    assert all(c.id.startswith("zjut-chain-") for c in cases)


def test_validate_chain_dataset_clean():
    assert validate_chain_dataset(load_chain_cases()) == []


def test_validate_rejects_knowledge_without_entry_ids():
    from campus_desk.eval.models import ScriptedCase

    bad = ScriptedCase(
        id="x-1",
        category="knowledge",
        student_input="放假？",
        intent="knowledge",
        expected_route="knowledge",
        expected_keywords=["寒假"],
    )
    problems = validate_chain_dataset([bad])
    assert any("expected_entry_ids" in p for p in problems)


def test_validate_rejects_tool_without_tool_name():
    from campus_desk.eval.models import ScriptedCase

    bad = ScriptedCase(
        id="x-2",
        category="tool_query",
        student_input="有空教室吗",
        intent="tool_query",
        expected_route="tool_query",
        expected_keywords=["空闲教室"],
    )
    problems = validate_chain_dataset([bad])
    assert any("expected_tool" in p for p in problems)


def test_validate_allows_handoff_without_entry_ids():
    from campus_desk.eval.models import ScriptedCase

    ok = ScriptedCase(
        id="x-3",
        category="knowledge",
        student_input="奖学金标准？",
        intent="knowledge",
        expected_route="knowledge",
        expected_outcome="handoff",
        expected_keywords=["工作人员"],
    )
    assert validate_chain_dataset([ok]) == []


def test_validate_rejects_inject_error_without_degraded():
    """inject_error 剧本必须 expected_outcome=degraded（注入机制一致性）。"""
    from campus_desk.eval.models import ScriptedCase

    bad = ScriptedCase(
        id="x-4",
        category="tool_query",
        student_input="3号楼下午有空教室吗",
        intent="tool_query",
        expected_route="tool_query",
        expected_outcome="answer",
        expected_tool="query_empty_rooms",
        expected_keywords=["空闲教室"],
        inject_error="db",
    )
    problems = validate_chain_dataset([bad])
    assert any("degraded" in p for p in problems)


def test_validate_rejects_degraded_without_inject_error():
    """degraded 剧本必须带 inject_error（否则无法注入数据源异常）。"""
    from campus_desk.eval.models import ScriptedCase

    bad = ScriptedCase(
        id="x-5",
        category="tool_query",
        student_input="3号楼下午有空教室吗",
        intent="tool_query",
        expected_route="tool_query",
        expected_outcome="degraded",
        expected_tool="query_empty_rooms",
        expected_keywords=["查不到"],
    )
    problems = validate_chain_dataset([bad])
    assert any("inject_error" in p for p in problems)
