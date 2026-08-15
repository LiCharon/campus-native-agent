"""追问决策器测试（M1-T5）：ask/handoff 决策 + 重试/容错/兜底。

覆盖：正常 ask / 正常 handoff / LLM 异常兜底 / 坏 JSON 重试 /
```json 围栏容错 / 前后缀容错 / llm=None 直通 handoff / 非法 action 枚举拒绝。
全部用 conftest 的 fake LLM stub，不依赖外部；真 LLM 集成验证走评测运行器。
"""

import json

from conftest import FakeStructuredLLM

from campus_desk.knowledge.decide import ClarifyDecider


def _ask_json() -> str:
    return json.dumps(
        {"action": "ask", "questions": ["您问的是哪个校区？"], "reply": "请补充校区信息。", "summary": "问校区"},
        ensure_ascii=False,
    )


def _handoff_json() -> str:
    return json.dumps(
        {"action": "handoff", "questions": [], "reply": "该问题需人工处理。", "summary": "转人工"},
        ensure_ascii=False,
    )


# ---------- 正常路径 ----------


def test_decide_ask_when_info_incomplete():
    fake = FakeStructuredLLM([_ask_json()])
    d = ClarifyDecider(llm=fake).decide(history=[], user_text="图书馆几点开门", missed=True)
    assert d.action == "ask"
    assert d.questions == ["您问的是哪个校区？"]
    assert fake.calls == 1  # 成功路径只调一次


def test_decide_handoff_when_question_complete():
    fake = FakeStructuredLLM([_handoff_json()])
    d = ClarifyDecider(llm=fake).decide(history=[], user_text="量子力学怎么学", missed=True)
    assert d.action == "handoff"


# ---------- 重试 / 容错 ----------


def test_decide_retries_bad_json_then_ask():
    fake = FakeStructuredLLM(["这不是JSON", _ask_json()])
    d = ClarifyDecider(llm=fake).decide(history=[], user_text="图书馆几点开门", missed=True)
    assert d.action == "ask"
    assert fake.calls == 2  # 首次解析失败 → 重试 1 次成功


def test_decide_tolerates_markdown_code_block():
    fake = FakeStructuredLLM([f"```json\n{_handoff_json()}\n```"])
    d = ClarifyDecider(llm=fake).decide(history=[], user_text="量子力学怎么学", missed=True)
    assert d.action == "handoff"


def test_decide_tolerates_padding_text():
    fake = FakeStructuredLLM([f"好的\n{_ask_json()}结束"])
    d = ClarifyDecider(llm=fake).decide(history=[], user_text="图书馆几点开门", missed=True)
    assert d.action == "ask"


# ---------- 兜底 ----------


def test_decide_fallback_on_llm_error():
    fake = FakeStructuredLLM([RuntimeError("down")])
    d = ClarifyDecider(llm=fake).decide(history=[], user_text="随便", missed=True)
    assert d.action == "handoff"  # 兜底转人工，绝不裸奔


def test_decide_none_llm_handoff_without_error():
    d = ClarifyDecider(llm=None).decide(history=[], user_text="随便", missed=True)
    assert d.action == "handoff"


def test_decide_rejects_unknown_action():
    fake = FakeStructuredLLM(['{"action":"banana","questions":[],"reply":"x","summary":"y"}'])
    d = ClarifyDecider(llm=fake).decide(history=[], user_text="随便", missed=True)
    assert d.action == "handoff"  # Literal 校验拒绝垃圾动作 → 重试耗尽 → 兜底 handoff
