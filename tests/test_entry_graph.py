"""EntryGraph 分流测试（M1-T7 新枚举）：识别 → 门控 → 路由 三段行为锁定。

用 FakeIntentClassifier 注入固定识别结果，图内不依赖 LLM。
覆盖：4 类新意图路由 / 低置信转人工 / other 恒转人工 / 多意图取主+次要提示 /
0.7 边界放行（>=0.7 且非 other）。
"""

from conftest import FakeIntentClassifier

from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.intent import IntentResult
from campus_desk.entry.routes import HUMAN_HANDOFF, KNOWLEDGE, MULTI_INTENT, TOOL_QUERY


def run(intent: str, confidence: float = 0.9, secondary: list[str] | None = None) -> dict:
    classifier = FakeIntentClassifier(
        IntentResult(intent=intent, confidence=confidence, secondary_intents=secondary or [])
    )
    graph = build_entry_graph(classifier=classifier)
    return graph.invoke({"user_input": "测试输入"})


# ---------- 4 类新意图路由 ----------


def test_route_knowledge():
    out = run("knowledge", 0.9)
    assert out["route"] == KNOWLEDGE
    assert "查询" in out["reply"]


def test_route_tool_query():
    out = run("tool_query", 0.9)
    assert out["route"] == TOOL_QUERY
    assert "建设中" in out["reply"]


def test_route_multi_intent_with_secondary_prompt():
    # 多意图：取主意图 multi_intent，次要意图给"可继续提问"提示
    out = run("multi_intent", 0.9, secondary=["knowledge"])
    assert out["route"] == MULTI_INTENT
    assert "可以继续问我" in out["reply"]


# ---------- 门控：低置信 / other → 人工 ----------


def test_gate_low_confidence_handoff():
    out = run("knowledge", 0.4)
    assert out["route"] == HUMAN_HANDOFF
    assert "人工" in out["reply"]


def test_other_intent_routes_to_human_handoff():
    # 其他/闲聊：高置信也转人工（人机协同不硬答）
    out = run("other", 0.95)
    assert out["route"] == HUMAN_HANDOFF
    assert "人工" in out["reply"]


def test_other_low_confidence_handoff():
    out = run("other", 0.4)
    assert out["route"] == HUMAN_HANDOFF


# ---------- 0.7 置信度边界 ----------


def test_threshold_boundary_at_070_passes():
    # >=0.7 且非 other → 放行到对应路由
    out = run("knowledge", 0.7)
    assert out["route"] == KNOWLEDGE


def test_threshold_boundary_below_070_handoff():
    out = run("knowledge", 0.69)
    assert out["route"] == HUMAN_HANDOFF


# ---------- 多意图提示 ----------


def test_no_secondary_no_extra_prompt():
    out = run("knowledge", 0.9)
    assert "可以继续问我" not in out["reply"]
