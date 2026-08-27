"""M12 B2：统一最近 8 轮上下文窗口。

验收：
- _recent_history 取最近 N 条 user 文本、显式排除当前消息、升序
- 意图识别 / 追问决策 / 工具选择 三调用点把 recent 注入 LLM human 消息（理解指代）
- recent 不进入检索拼接（检索只看当前+图内 ≤3 追问轮，本文件不测，属既有逻辑）
"""

from conftest import FakeStructuredLLM, FakeToolLLM

from campus_desk.api.graphs import _recent_history
from campus_desk.db.models import Conversation, Message
from campus_desk.entry.intent import IntentClassifier
from campus_desk.knowledge.decide import ClarifyDecider
from campus_desk.query.graph import _call_tools


def test_recent_history_excludes_current_and_limits(db_session_factory):
    """最近 N 条 user 文本、排除当前消息、升序；超窗口丢弃最旧。"""
    with db_session_factory() as s, s.begin():
        s.add(Conversation(id="c-rh", user_id="student-001", thread_id="t-rh"))
        msgs = [Message(conversation_id="c-rh", role="user", content=f"q{i}") for i in range(10)]
        s.add_all(msgs)
        s.flush()
        current_id = msgs[-1].id  # 最后一条 q9 即"当前消息"

    recent = _recent_history("t-rh", db_session_factory, current_id, 8)
    # 排除当前(q9)后取最近 8 条 → q1..q8 升序；q0 超出窗口、q9 被排除
    assert recent == [f"q{i}" for i in range(1, 9)], recent
    assert "q0" not in recent  # 超出窗口
    assert "q9" not in recent  # 当前消息被排除


def test_recent_history_no_exclude_when_none(db_session_factory):
    """current_message_id=None 时不排除任何条（取最近 N 条全部）。"""
    with db_session_factory() as s, s.begin():
        s.add(Conversation(id="c-rh2", user_id="student-001", thread_id="t-rh2"))
        for i in range(3):
            s.add(Message(conversation_id="c-rh2", role="user", content=f"m{i}"))
    recent = _recent_history("t-rh2", db_session_factory, None, 8)
    assert recent == ["m0", "m1", "m2"]


def test_intent_injects_recent(db_session_factory):
    """意图识别把 recent 拼入 human 消息（理解指代）。"""
    fake = FakeStructuredLLM(
        ['{"intent":"knowledge","confidence":0.9,"primary_intent":null,"secondary_intents":[],"reason":"x"}']
    )
    clf = IntentClassifier(llm=fake)
    clf.classify("这栋楼几点关门", recent=["图书馆在几号楼", "校历什么时候出"])
    human = fake.last_messages[-1]
    assert human[0] == "human"
    assert "近期对话" in human[1]
    assert "图书馆在几号楼" in human[1]


def test_decide_injects_recent(db_session_factory):
    """追问决策把 recent 作为背景段拼入 context（理解指代）。"""
    fake = FakeStructuredLLM(
        ['{"action":"ask","questions":["哪个楼栋"],"reply":"请补充","summary":"x"}']
    )
    decider = ClarifyDecider(llm=fake)
    decider.decide([], "这栋楼几点关门", missed=True, recent=["图书馆在几号楼"])
    human = fake.last_messages[-1]
    assert "近期对话" in human[1]
    assert "图书馆在几号楼" in human[1]


def test_query_call_tools_injects_recent():
    """工具选择把 recent 拼入 human 消息（理解指代，如'那栋楼'）。"""

    class _FakeDeps:
        def __init__(self, llm):
            self.llm = llm

        def query_prompt(self):
            return "你是校园查询助手"

    fake = FakeToolLLM([])
    deps = _FakeDeps(fake)
    _call_tools(deps, "这栋楼几点关门", recent=["图书馆在几号楼"])
    human = fake.last_messages[-1]
    assert "近期对话" in human[1]
    assert "图书馆在几号楼" in human[1]
