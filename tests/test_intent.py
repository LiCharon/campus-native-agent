"""IntentClassifier 三层防线单测：LLM 结构化输出 → 重试 1 次 → 规则兜底。

全部用 conftest 的 fake LLM stub，不依赖外部；真 LLM 集成验证走评测运行器。
"""

import json

from conftest import FakeStructuredLLM

from campus_desk.entry.intent import IntentClassifier


def ok_content(intent: str, confidence: float = 0.9, secondary: list[str] | None = None) -> str:
    return json.dumps(
        {
            "intent": intent,
            "confidence": confidence,
            "secondary_intents": secondary or [],
            "reason": "测试",
        },
        ensure_ascii=False,
    )


# ---------- 第一层：LLM 结构化输出正常 ----------


def test_classify_returns_structured_result():
    fake = FakeStructuredLLM([ok_content("repair", 0.95)])
    classifier = IntentClassifier(llm=fake)
    result = classifier.classify("宿舍灯坏了")
    assert result.intent == "repair"
    assert result.confidence == 0.95
    assert fake.calls == 1  # 成功路径只调一次


def test_classify_keeps_secondary_intents():
    fake = FakeStructuredLLM([ok_content("repair", 0.9, secondary=["consult"])])
    result = IntentClassifier(llm=fake).classify("灯坏了顺便问下密码")
    assert result.intent == "repair"
    assert result.secondary_intents == ["consult"]


def test_parse_tolerates_markdown_code_block():
    content = f"```json\n{ok_content('consult', 0.8)}\n```"
    fake = FakeStructuredLLM([content])
    result = IntentClassifier(llm=fake).classify("怎么查密码")
    assert result.intent == "consult"


def test_parse_tolerates_padding_text():
    content = f"好的，这是结果：{ok_content('complaint', 0.85)}"
    fake = FakeStructuredLLM([content])
    result = IntentClassifier(llm=fake).classify("我要投诉")
    assert result.intent == "complaint"


# ---------- 第二层：解析失败重试 1 次 ----------


def test_retries_once_on_bad_json_then_succeeds():
    fake = FakeStructuredLLM(["这不是JSON格式的输出", ok_content("consult", 0.8)])
    classifier = IntentClassifier(llm=fake)
    result = classifier.classify("怎么查校园网密码")
    assert result.intent == "consult"
    assert fake.calls == 2  # 失败后重试一次


def test_retries_once_on_llm_exception_then_succeeds():
    fake = FakeStructuredLLM([RuntimeError("网络超时"), ok_content("complaint", 0.85)])
    result = IntentClassifier(llm=fake).classify("我要投诉食堂")
    assert result.intent == "complaint"
    assert fake.calls == 2


# ---------- 第三层：连续失败 → 规则兜底 ----------


def test_rule_fallback_when_both_attempts_fail():
    fake = FakeStructuredLLM(["坏JSON1", "坏JSON2"])
    result = IntentClassifier(llm=fake).classify("宿舍灯坏了不亮")
    assert result.intent == "repair"  # 关键词"坏"命中
    assert result.confidence == 0.5  # 兜底置信度固定低值（触发门控转人工）
    assert fake.calls == 2  # 不再无限重试


def test_rule_fallback_on_llm_exception():
    fake = FakeStructuredLLM([RuntimeError("服务不可用"), RuntimeError("仍不可用")])
    result = IntentClassifier(llm=fake).classify("我要投诉食堂阿姨态度")
    assert result.intent == "complaint"
    assert result.confidence == 0.5


def test_rule_fallback_consult_keywords():
    fake = FakeStructuredLLM(["坏JSON"])
    result = IntentClassifier(llm=fake).classify("怎么修改校园网密码")
    assert result.intent == "consult"


def test_rule_fallback_miss_returns_other():
    fake = FakeStructuredLLM(["坏JSON"])
    result = IntentClassifier(llm=fake).classify("今天天气真不错")
    assert result.intent == "other"
    assert result.confidence == 0.5


# ---------- 边界 ----------


def test_json_missing_required_field_treated_as_failure():
    # JSON 缺必填字段（如 confidence）→ pydantic 校验失败 → 视为失败走兜底
    fake = FakeStructuredLLM(['{"intent": "repair"}'])
    result = IntentClassifier(llm=fake).classify("灯坏了")
    assert result.intent == "repair"
    assert result.confidence == 0.5


def test_json_invalid_intent_value_treated_as_failure():
    # intent 枚举外值 → Literal 校验失败 → 兜底
    fake = FakeStructuredLLM(['{"intent": "banana", "confidence": 0.9}'])
    result = IntentClassifier(llm=fake).classify("灯坏了")
    assert result.intent == "repair"
    assert result.confidence == 0.5


def test_empty_input_not_crash():
    fake = FakeStructuredLLM(["坏JSON"])
    result = IntentClassifier(llm=fake).classify("")
    assert result.intent == "other"
