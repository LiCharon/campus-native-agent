"""EntryGraph 分流测试：识别 → 门控 → 路由 三段行为锁定。

用 FakeIntentClassifier 注入固定识别结果，图内不依赖 LLM。
覆盖：4 意图路由 / 低置信转人工 / 其他意图转人工 / 多意图取主+次要提示。
"""

from conftest import FakeIntentClassifier

from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.intent import IntentResult
from campus_desk.entry.routes import COMPLAINT, CONSULT, HUMAN_HANDOFF, REPAIR


def run(intent: str, confidence: float = 0.9, secondary: list[str] | None = None) -> dict:
    classifier = FakeIntentClassifier(
        IntentResult(intent=intent, confidence=confidence, secondary_intents=secondary or [])
    )
    graph = build_entry_graph(classifier=classifier)
    return graph.invoke({"user_input": "测试输入"})


# ---------- 4 意图路由 ----------


def test_high_confidence_repair_routes_to_repair():
    out = run("repair", 0.95)
    assert out["route"] == REPAIR
    assert "报修" in out["reply"]


def test_high_confidence_consult_routes_to_consult():
    out = run("consult", 0.9)
    assert out["route"] == CONSULT
    assert "咨询" in out["reply"]


def test_high_confidence_complaint_routes_to_complaint():
    out = run("complaint", 0.9)
    assert out["route"] == COMPLAINT
    assert "投诉" in out["reply"]


# ---------- 门控：低置信 / 其他 → 人工 ----------


def test_low_confidence_routes_to_human_handoff():
    out = run("repair", 0.5)
    assert out["route"] == HUMAN_HANDOFF
    assert "人工" in out["reply"]


def test_threshold_boundary_below_070():
    out = run("consult", 0.69)
    assert out["route"] == HUMAN_HANDOFF


def test_threshold_boundary_at_070():
    out = run("consult", 0.7)
    assert out["route"] == CONSULT


def test_other_intent_routes_to_human_handoff():
    # 其他/闲聊：人机协同不硬答 → 转人工
    out = run("other", 0.95)
    assert out["route"] == HUMAN_HANDOFF


# ---------- 多意图 ----------


def test_multi_intent_takes_main_and_prompts_secondary():
    out = run("repair", 0.9, secondary=["consult"])
    assert out["route"] == REPAIR  # 取主意图
    assert "继续" in out["reply"] and "咨询" in out["reply"]  # 次要问题提示可继续提问


def test_no_secondary_no_extra_prompt():
    out = run("repair", 0.9)
    assert "继续" not in out["reply"]
