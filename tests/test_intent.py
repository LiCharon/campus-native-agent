"""IntentClassifier 三层防线单测：LLM 结构化输出 → 重试 1 次 → 规则兜底。

意图枚举为 ZJUT 4 类（knowledge/tool_query/multi_intent/other）：
- knowledge: 校园知识问答（校历/放假/流程/开放时间/联系方式等）
- tool_query: 动态数据查询（空教室/自习室/座位/课表等，需调用查询工具）
- multi_intent: 一句话包含多个独立问题
- other: 闲聊/问候/超出校园服务范围

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


def test_classify_knowledge_with_fake_llm():
    fake = FakeStructuredLLM([ok_content("knowledge", 0.9)])
    classifier = IntentClassifier(llm=fake)
    result = classifier.classify("什么时候放寒假？")
    assert result.intent == "knowledge"
    assert result.confidence == 0.9
    assert fake.calls == 1  # 成功路径只调一次


def test_classify_keeps_secondary_intents():
    fake = FakeStructuredLLM([ok_content("multi_intent", 0.85, secondary=["knowledge"])])
    result = IntentClassifier(llm=fake).classify("成绩单怎么打？顺便问下校历")
    assert result.intent == "multi_intent"
    assert result.secondary_intents == ["knowledge"]


def test_parse_tolerates_markdown_code_block():
    content = f"```json\n{ok_content('tool_query', 0.8)}\n```"
    fake = FakeStructuredLLM([content])
    result = IntentClassifier(llm=fake).classify("3号楼有空教室吗")
    assert result.intent == "tool_query"


def test_parse_tolerates_padding_text():
    content = f"好的，这是结果：{ok_content('other', 0.85)}"
    fake = FakeStructuredLLM([content])
    result = IntentClassifier(llm=fake).classify("今天天气不错")
    assert result.intent == "other"


# ---------- 第二层：解析失败重试 1 次 ----------


def test_retries_once_on_bad_json_then_succeeds():
    fake = FakeStructuredLLM(["这不是JSON格式的输出", ok_content("knowledge", 0.8)])
    classifier = IntentClassifier(llm=fake)
    result = classifier.classify("校历什么时候出？")
    assert result.intent == "knowledge"
    assert fake.calls == 2  # 失败后重试一次


def test_retries_once_on_llm_exception_then_succeeds():
    fake = FakeStructuredLLM([RuntimeError("网络超时"), ok_content("tool_query", 0.85)])
    result = IntentClassifier(llm=fake).classify("图书馆有座位吗")
    assert result.intent == "tool_query"
    assert fake.calls == 2


# ---------- 第三层：连续失败 → 规则兜底 ----------


def test_rule_fallback_hits_knowledge_keyword():
    # LLM 抛异常 → 规则兜底："校历"命中 knowledge 关键词
    fake = FakeStructuredLLM([RuntimeError("down"), RuntimeError("down")])
    result = IntentClassifier(llm=fake).classify("校历什么时候出？")
    assert result.intent == "knowledge"
    assert result.confidence < 0.7  # 兜底低置信 → 门控转人工


def test_rule_fallback_hits_tool_query_keyword():
    fake = FakeStructuredLLM(["坏JSON", "坏JSON"])
    result = IntentClassifier(llm=fake).classify("3号楼有空教室吗")
    assert result.intent == "tool_query"
    assert result.confidence < 0.7


def test_rule_fallback_hits_multi_intent_keyword():
    # "顺便"命中 multi_intent 关键词 → 优先判定多意图（不参与单意图分数竞争）
    fake = FakeStructuredLLM(["坏JSON", "坏JSON"])
    result = IntentClassifier(llm=fake).classify("一卡通怎么补办？顺便问下校历")
    assert result.intent == "multi_intent"
    assert result.confidence < 0.7


def test_rule_fallback_miss_returns_other():
    fake = FakeStructuredLLM(["坏JSON"])
    result = IntentClassifier(llm=fake).classify("今天天气真不错")
    assert result.intent == "other"
    assert result.confidence == 0.5


# ---------- 边界 ----------


def test_json_missing_required_field_treated_as_failure():
    # JSON 缺必填字段（如 confidence）→ pydantic 校验失败 → 视为失败走兜底
    fake = FakeStructuredLLM(['{"intent": "knowledge"}'])
    result = IntentClassifier(llm=fake).classify("校历什么时候出？")
    assert result.intent == "knowledge"
    assert result.confidence == 0.5


def test_json_invalid_intent_value_treated_as_failure():
    # intent 枚举外值 → Literal 校验失败 → 兜底
    fake = FakeStructuredLLM(['{"intent": "banana", "confidence": 0.9}'])
    result = IntentClassifier(llm=fake).classify("校历什么时候出？")
    assert result.intent == "knowledge"
    assert result.confidence == 0.5


def test_empty_input_not_crash():
    fake = FakeStructuredLLM(["坏JSON"])
    result = IntentClassifier(llm=fake).classify("")
    assert result.intent == "other"
