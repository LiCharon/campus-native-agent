"""M17A-T6：KnowledgeGraph.collect 接线生成节点。

- 注入 generator ⇒ reply 来自生成器（hits/question 正确透传）
- 生成器抛错 ⇒ 回退 assemble_answer 模板（可用性不退化）
- 默认生成器 + 无 LLM（conftest autouse 掐断）⇒ 同样回退模板
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.knowledge.graph import build_knowledge_graph


class FakeDecider:
    def __init__(self, sequence):
        self.sequence = list(sequence)

    def decide(self, history, user_text, missed, recent=None):
        return self.sequence.pop(0)


def _seed_one(session_factory):
    from campus_desk.db.models import KnowledgeEntry

    with session_factory() as s, s.begin():
        s.query(KnowledgeEntry).delete()
        s.add(
            KnowledgeEntry(
                domain="教务",
                keywords="校历,寒假,时候",
                question="放假？",
                type="info",
                answer="寒假以通知为准。",
            )
        )


def _invoke(graph, thread="t-gen"):
    return graph.invoke({"user_input": "什么时候放寒假？"}, {"configurable": {"thread_id": thread}})


def test_collect_uses_injected_generator(db_session_factory):
    _seed_one(db_session_factory)
    seen = {}

    def fake_gen(hits, question):
        seen["hits"], seen["q"] = hits, question
        return f"FAKE 整合回答 [1]（{len(hits)} 条）"

    graph = build_knowledge_graph(
        db_session_factory,
        decider=FakeDecider([]),
        checkpointer=InMemorySaver(),
        generate_fn=fake_gen,
    )
    out = _invoke(graph)
    assert out["outcome"] == "answer"
    assert out["reply"] == "FAKE 整合回答 [1]（1 条）"
    assert seen["q"] == "什么时候放寒假？"
    assert [h["id"] for h in seen["hits"]] == [out["hits"][0]]


def test_collect_falls_back_on_generator_error(db_session_factory):
    _seed_one(db_session_factory)

    def boom(hits, question):
        raise TimeoutError("LLM 超时")

    graph = build_knowledge_graph(
        db_session_factory,
        decider=FakeDecider([]),
        checkpointer=InMemorySaver(),
        generate_fn=boom,
    )
    out = _invoke(graph)
    assert out["outcome"] == "answer"
    assert out["reply"] == "寒假以通知为准。"  # 模板：单条直返 answer


def test_collect_multi_hits_template_numbered(db_session_factory):
    from campus_desk.db.models import KnowledgeEntry

    with db_session_factory() as s, s.begin():
        s.query(KnowledgeEntry).delete()
        s.add(
            KnowledgeEntry(domain="教务", keywords="校历,寒假,时候", question="放假？", type="info", answer="寒假以通知为准。")
        )
        s.add(
            KnowledgeEntry(domain="图书馆", keywords="闭馆,时间,几点", question="几点闭馆？", type="info", answer="22:00 闭馆。")
        )

    def boom(hits, question):
        raise RuntimeError("LLM 不可用")

    graph = build_knowledge_graph(
        db_session_factory,
        decider=FakeDecider([]),
        checkpointer=InMemorySaver(),
        generate_fn=boom,
    )
    out = graph.invoke(
        {"user_input": "寒假什么时候 几点闭馆"}, {"configurable": {"thread_id": "t-gen-multi"}}
    )
    assert out["reply"].startswith("为您找到以下相关信息：\n1. ")


def test_default_generator_falls_back_without_llm(db_session_factory, monkeypatch):
    """默认生成器（generate_fn=None）+ 无 LLM：generate_answer 抛 RuntimeError ⇒ 模板。"""
    from campus_desk.knowledge import generator

    monkeypatch.setattr(generator, "_get_llm", lambda: None)  # 与 conftest autouse 同口径
    _seed_one(db_session_factory)
    graph = build_knowledge_graph(
        db_session_factory, decider=FakeDecider([]), checkpointer=InMemorySaver()
    )
    out = _invoke(graph)
    assert out["outcome"] == "answer"
    assert out["reply"] == "寒假以通知为准。"
