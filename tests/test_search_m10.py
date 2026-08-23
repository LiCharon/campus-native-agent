"""M10 检索层测试：三档降级结构一致 + retrieve 工具注册 + Qdrant 不可用兜底关键词。

不依赖网络/模型：fastembed 相关用例通过 monkeypatch 注入假向量，离线可跑；
真实模型/网络缺失时相关路径自动回落到关键词档，保证 309 基线零改动变绿。
"""

from __future__ import annotations

import numpy as np
import pytest

from campus_desk.db.models import KnowledgeEntry
from campus_desk.knowledge import embeddings, vector_store
from campus_desk.knowledge.search import assemble_answer, search_knowledge
from campus_desk.query.tools import TOOL_FUNCS, TOOL_SCHEMAS

_EXPECTED_KEYS = {"id", "domain", "keywords", "question", "type", "answer"}


@pytest.fixture
def seeded_kb(db_session_factory):
    with db_session_factory() as s, s.begin():
        s.add(
            KnowledgeEntry(
                domain="图书馆",
                keywords="闭馆,关门",
                question="图书馆几点闭馆",
                type="info",
                answer="22:00 闭馆",
            )
        )
        s.add(
            KnowledgeEntry(
                domain="生活服务",
                keywords="校园卡,挂失",
                question="校园卡丢了怎么办",
                type="process",
                answer="先挂失再补办",
            )
        )
    return db_session_factory


def test_retrieve_tool_registered():
    assert "retrieve_knowledge" in TOOL_FUNCS
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert "retrieve_knowledge" in names
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "retrieve_knowledge")
    assert schema["function"]["strict"] is True
    assert "query" in schema["function"]["parameters"]["required"]


def test_keyword_fallback_when_qdrant_and_embed_unavailable(seeded_kb, monkeypatch):
    # 强制走 Tier3 关键词：Qdrant 不可用 + 嵌入不可用
    monkeypatch.setattr(vector_store, "is_available", lambda: False)

    def _raise(*_a, **_k):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_dense", _raise)

    hits = search_knowledge(seeded_kb, "图书馆闭馆时间")
    assert hits, "关键词档应召回『图书馆几点闭馆』"
    assert set(hits[0].keys()) == _EXPECTED_KEYS
    assert any(h["question"] == "图书馆几点闭馆" for h in hits)


def test_mysql_dense_tier_ranking(seeded_kb, monkeypatch):
    # 注入假嵌入：query 向量 = 命中条目的稠密向量（余弦=1），另一条正交（余弦=0）
    monkeypatch.setattr(vector_store, "is_available", lambda: False)

    q_vec = np.zeros(512, dtype=np.float32)
    q_vec[0] = 1.0
    hit_vec = q_vec.copy()
    other_vec = np.zeros(512, dtype=np.float32)
    other_vec[1] = 1.0

    def _fake_embed(texts):
        return q_vec.reshape(1, -1)

    monkeypatch.setattr(embeddings, "embed_dense", _fake_embed)

    with seeded_kb() as s, s.begin():
        for r in s.query(KnowledgeEntry).all():
            if r.question == "图书馆几点闭馆":
                r.dense_vector = embeddings.dense_to_json(hit_vec)
            elif r.question == "校园卡丢了怎么办":
                r.dense_vector = embeddings.dense_to_json(other_vec)

    hits = search_knowledge(seeded_kb, "图书馆相关")
    assert hits, "MySQL 稠密档应召回条目"
    assert set(hits[0].keys()) == _EXPECTED_KEYS
    assert hits[0]["question"] == "图书馆几点闭馆"


def test_assemble_answer_single_and_multi():
    assert assemble_answer([{"answer": "A"}]) == "A"
    assert "相关信息" in assemble_answer([{"answer": "A"}, {"answer": "B"}])
    assert assemble_answer([]) == ""
