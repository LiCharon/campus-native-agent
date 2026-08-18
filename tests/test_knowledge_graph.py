"""KnowledgeGraph 测试（M1-T6）：collect→wait 双节点 ping-pong 知识问答图。

覆盖：命中直答 / 未命中追问→补充后命中 / decider 判 handoff 存 bad_cases /
追问超限 MAX_CLARIFY_ROUNDS 强制 handoff。全部注入 FakeDecider，不依赖 LLM。

种子关键词说明（对齐 search_knowledge 的子串匹配语义）：
- test_hit 用 "校历,寒假"：查询"什么时候放寒假？"含子串"寒假"（"校历"不含）
- test_miss 用 "开放时间,校图书馆"：首轮"图书馆几点开门"不命中，
  补充"校图书馆"后合并文本命中（"开馆"与"开门"非子串关系，原关键词永不可命中）
"""

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.knowledge.graph import build_knowledge_graph


def _clear_knowledge(session_factory):
    """测试隔离（T9）：清空全局 36 条种子，保证图流程只看到测试自己的条目。"""
    from campus_desk.db.models import KnowledgeEntry

    with session_factory() as s, s.begin():
        s.query(KnowledgeEntry).delete()


class FakeDecider:
    def __init__(self, sequence):
        self.sequence = list(sequence)

    def decide(self, history, user_text, missed):
        return self.sequence.pop(0)


def test_hit_answers_directly(db_session_factory):
    from campus_desk.db.models import KnowledgeEntry

    _clear_knowledge(db_session_factory)  # 否则 36 条种子里同 question 条目抢先命中
    with db_session_factory() as s, s.begin():
        s.add(
            KnowledgeEntry(
                domain="教务",
                keywords="校历,寒假",
                question="放假？",
                type="info",
                answer="寒假以通知为准。",
            )
        )
    graph = build_knowledge_graph(
        db_session_factory, decider=FakeDecider([]), checkpointer=InMemorySaver()
    )
    # langgraph 1.x：compile(checkpointer=...) 后 invoke 必须带 thread_id（即使不触发 interrupt）
    cfg = {"configurable": {"thread_id": "t-hit"}}
    out = graph.invoke({"user_input": "什么时候放寒假？"}, cfg)
    assert out["finished"] is True
    assert "寒假" in out["reply"]


def test_miss_asks_then_answers_after_clarify(db_session_factory):
    from langgraph.types import Command

    from campus_desk.db.models import KnowledgeEntry
    from campus_desk.knowledge.decide import ClarifyDecision

    _clear_knowledge(
        db_session_factory
    )  # 否则 36 条种子"图书馆几点开门？"首轮直答，miss 流程走不到
    with db_session_factory() as s, s.begin():
        s.add(
            KnowledgeEntry(
                domain="图书馆",
                keywords="开放时间,校图书馆",
                question="图书馆几点开门？",
                type="info",
                answer="8:00-22:00。",
            )
        )
    decider = FakeDecider(
        [
            ClarifyDecision(
                action="ask",
                questions=["您问的是哪个图书馆？"],
                reply="请补充。",
                summary="问图书馆",
            ),
        ]
    )
    graph = build_knowledge_graph(db_session_factory, decider=decider, checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t1"}}
    first = graph.invoke({"user_input": "图书馆几点开门"}, cfg)
    assert first["finished"] is False
    assert first["pending_question"]
    # 学生补充后重检索命中
    second = graph.invoke(Command(resume="校图书馆"), cfg)
    assert second["finished"] is True
    assert "8:00" in second["reply"]


def test_handoff_saves_bad_case(db_session_factory):
    from campus_desk.db.models import BadCase
    from campus_desk.knowledge.decide import ClarifyDecision

    decider = FakeDecider(
        [
            ClarifyDecision(
                action="handoff", questions=[], reply="该问题需人工处理。", summary="转人工"
            ),
        ]
    )
    graph = build_knowledge_graph(db_session_factory, decider=decider, checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t-handoff"}}
    out = graph.invoke({"user_input": "量子力学怎么学"}, cfg)
    assert out["finished"] is True
    assert out["outcome"] == "handoff"
    with db_session_factory() as s:
        row = s.query(BadCase).one()
        assert row.status == "PENDING"
        assert row.question == "量子力学怎么学"


def test_max_clarify_rounds_forced_handoff(db_session_factory):
    from langgraph.types import Command

    from campus_desk.db.models import BadCase
    from campus_desk.knowledge.decide import ClarifyDecision
    from campus_desk.knowledge.graph import MAX_CLARIFY_ROUNDS

    # 连续 MAX_CLARIFY_ROUNDS+1 次 ask：前 3 次触发追问，第 4 次超限强制 handoff
    asks = [
        ClarifyDecision(action="ask", questions=["请补充。"], reply="请补充。", summary="追问"),
    ] * (MAX_CLARIFY_ROUNDS + 1)
    graph = build_knowledge_graph(
        db_session_factory, decider=FakeDecider(asks), checkpointer=InMemorySaver()
    )
    cfg = {"configurable": {"thread_id": "t-max"}}
    out = graph.invoke({"user_input": "空调维修流程"}, cfg)
    assert out["finished"] is False
    for _ in range(MAX_CLARIFY_ROUNDS):
        out = graph.invoke(Command(resume="补充信息"), cfg)
    assert out["finished"] is True
    assert out["outcome"] == "handoff"
    with db_session_factory() as s:
        assert s.query(BadCase).one().status == "PENDING"


def test_clarify_merge_joins_all_history(db_session_factory):
    """T6 Minor：合并检索 join 全部 history（而非仅上一轮），早轮关键词不丢。

    三轮追问下，第 3 轮传给 decider 的合并文本必须仍含第 1 轮原话。
    旧实现 f"{history[-1]} {text}" 到第 3 轮会丢轮 1 的检索词。
    """
    from langgraph.types import Command

    from campus_desk.knowledge.decide import ClarifyDecision

    seen = []

    class RecordingDecider:
        def decide(self, history, user_text, missed):
            seen.append((list(history), user_text))
            return ClarifyDecision(
                action="ask", questions=["请补充。"], reply="请补充。", summary="追问"
            )

    graph = build_knowledge_graph(
        db_session_factory, decider=RecordingDecider(), checkpointer=InMemorySaver()
    )
    cfg = {"configurable": {"thread_id": "t-join"}}
    _clear_knowledge(db_session_factory)  # 否则 36 条种子"图书馆几点开门？"直答，decider 永不触发
    graph.invoke({"user_input": "图书馆几点开门"}, cfg)
    graph.invoke(Command(resume="南门那个"), cfg)
    graph.invoke(Command(resume="大一点的"), cfg)
    assert len(seen) == 3
    # 第 3 轮合并文本 = 轮1 + 轮2 + 轮3 全部历史
    assert "图书馆几点开门" in seen[2][1]
    assert "南门那个" in seen[2][1]
    assert "大一点的" in seen[2][1]
