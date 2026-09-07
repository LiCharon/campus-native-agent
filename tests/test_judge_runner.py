"""E1 judge_runner 单测（假 LLM/假图，真 LLM 不进 pytest）。

覆盖：judge 输出解析（含容错）/ 材料拼接与截断 / judge 消息装配（gold+拒答口径）/
run_case 得分记录 / 聚合 / 数据集加载与链路引用解析。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from campus_desk.eval import judge_runner as jr


def test_parse_judge_json_clean_and_fenced():
    assert jr._parse_judge_json('{"faithfulness": 4, "relevance": 5, "format": 3, "reason": "ok"}') == {
        "faithfulness": 4, "relevance": 5, "format": 3, "reason": "ok"
    }
    fenced = '```json\n{"faithfulness": 5, "relevance": 4, "format": 5, "reason": "好"}\n```'
    assert jr._parse_judge_json(fenced)["relevance"] == 4


def test_parse_judge_json_invalid_returns_none():
    assert jr._parse_judge_json("这不是JSON") is None
    assert jr._parse_judge_json('{"faithfulness": "高"}') is None  # 缺字段/类型不对


def test_material_truncates_long_answers():
    hits = [
        {"id": 1, "question": "Q1", "answer": "长" * 400},
        {"id": 2, "question": "Q2", "answer": "短答案"},
    ]
    text = jr._judge_material(hits)
    assert "长" * 300 in text and "长" * 301 not in text  # 截断到 300 字
    assert "1. Q1" in text and "2. Q2" in text
    assert "短答案" in text


def test_build_judge_messages_contains_gold_and_refusal():
    case = {
        "case_id": "zjut-chain-k-008",
        "query": "研究生导师怎么选？",
        "expected_outcome": "handoff",
        "expected_refusal": True,
        "gold_texts": [],
    }
    messages = jr._build_judge_messages(case, reply="已为您转人工。", outcome="handoff", hits=[])
    system, human = messages[0][1], messages[1][1]
    assert "faithfulness" in system and "0-5" in system.replace("０-５", "0-5")
    assert "学生问题：研究生导师怎么选？" in human
    assert "拒答" in human
    # 无检索结果时明确告知 judge（拒答场景的正常输入）
    assert "无检索结果" in human


def test_build_judge_messages_embeds_gold_texts():
    case = {
        "case_id": "zjut-chain-k-001",
        "query": "什么时候放寒假？",
        "expected_outcome": "answer",
        "expected_refusal": False,
        "gold_texts": [{"id": 1, "question": "什么时候放寒假？", "answer": "以学校官方通知为准。"}],
    }
    human = jr._build_judge_messages(
        case, reply="寒假时间以通知为准 [1]。", outcome="answer", hits=[{"id": 1, "question": "", "answer": ""}]
    )[1][1]
    assert "以学校官方通知为准" in human  # gold 答案进 judge 输入


class FakeKG:
    """kg.invoke(state, cfg) → 预设 state 的 stub（对齐 langgraph 编译图接口）。"""

    def __init__(self, state):
        self.state = state
        self.invoked = 0

    def invoke(self, payload, cfg):
        self.invoked += 1
        return dict(self.state)


def test_run_case_records_scores(monkeypatch):
    fake_kg = FakeKG({"reply": "寒假以官方通知为准 [1]。", "outcome": "answer", "hits": [1]})
    fake_judge = SimpleNamespace(
        invoke=lambda messages: SimpleNamespace(
            content='{"faithfulness": 5, "relevance": 5, "format": 5, "reason": "r"}'
        )
    )
    monkeypatch.setattr(
        jr, "_resolve_hits", lambda factory, ids: [{"id": 1, "question": "q", "answer": "a"}]
    )
    case = {
        "case_id": "zjut-chain-k-001",
        "query": "什么时候放寒假？",
        "expected_outcome": "answer",
        "expected_refusal": False,
        "gold_texts": [{"id": 1, "question": "q", "answer": "a"}],
    }
    out = jr._run_case(fake_kg, fake_judge, case)
    assert out["scores"] == {"faithfulness": 5, "relevance": 5, "format": 5}
    assert out["reply"].startswith("寒假")
    assert out["hit_texts"] and out["hit_texts"][0]["answer"] == "a"
    assert out["error"] is None


def test_run_case_judge_parse_failure_records_error():
    fake_kg = FakeKG({"reply": "r", "outcome": "answer", "hits": []})
    bad_llm = SimpleNamespace(invoke=lambda messages: SimpleNamespace(content="乱输出"))
    case = {"case_id": "x", "query": "q", "expected_outcome": "answer", "expected_refusal": False, "gold_texts": []}
    out = jr._run_case(fake_kg, bad_llm, case)
    assert out["scores"] is None
    assert out["error"] and "解析" in out["error"]


def test_aggregate_means_and_refusal_compliance():
    rows = [
        {"case_id": "a", "expected_refusal": False, "scores": {"faithfulness": 5, "relevance": 4, "format": 5}, "outcome": "answer", "reply": "有 [1]", "error": None},
        {"case_id": "b", "expected_refusal": True, "scores": {"faithfulness": 5, "relevance": 5, "format": 5}, "outcome": "handoff", "reply": "转人工", "error": None},
        {"case_id": "c", "expected_refusal": False, "scores": None, "outcome": "answer", "reply": "x", "error": "解析失败"},
    ]
    agg = jr._aggregate(rows)
    assert agg["n"] == 3
    assert agg["judge_errors"] == 1
    assert agg["means"]["faithfulness"] == pytest.approx(5.0)
    assert agg["means"]["relevance"] == pytest.approx(4.5)
    assert agg["refusal_ok_n"] == 1  # b 判定拒答成功（handoff）


def test_dataset_loads_and_refs_resolve(db_session_factory):
    cases = jr.load_cases()
    assert len(cases) == 12  # 8 引用 + 4 内联拒答
    ids = {c["case_id"] for c in cases}
    assert "zjut-chain-k-001" in ids and "zjut-chain-k-005" in ids
    assert "zjut-chain-k-009" not in ids and "zjut-chain-k-010" not in ids  # 死 gold 排除
    k001 = next(c for c in cases if c["case_id"] == "zjut-chain-k-001")
    assert k001["query"] == "什么时候放寒假？"
    jr._resolve_gold_texts(db_session_factory, cases)  # gold id → 种子库条目文本
    assert k001["gold_texts"] and k001["gold_texts"][0]["answer"]
    inline = [c for c in cases if c["case_id"].startswith("inline-refusal")]
    assert len(inline) == 4 and all(c["expected_refusal"] for c in inline)
