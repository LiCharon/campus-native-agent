"""M17A-T1：检索 score 贯穿三档。

BUG-004 修复前置——三档返回结构统一带相关性分数：
Tier1 取 Qdrant point score（RRF 融合分）/ Tier2 取余弦 sim / Tier3 取关键词计分。
阈值过滤（T2–T4）依赖本字段；分数只作检索层内部判断用，不进对外契约
（retrieve 工具、sources chip 仍显式取键，不受新增字段影响）。
"""

from __future__ import annotations

import numpy as np
import pytest

from campus_desk.db.models import KnowledgeEntry
from campus_desk.knowledge import embeddings, vector_store
from campus_desk.knowledge.search import _mysql_dense_search, search_knowledge


@pytest.fixture
def seeded_kb(db_session_factory):
    with db_session_factory() as s, s.begin():
        s.query(KnowledgeEntry).delete()  # 测试隔离：清残留（共享库跨用例/跨运行）
        s.add(
            KnowledgeEntry(
                domain="教务",
                keywords="成绩,查询",
                question="成绩怎么查",
                type="index",
                answer="请登录教务系统查看。",
            )
        )
    return db_session_factory


def _fake_embed(dim_hit: bool):
    """返回 (embed_dense, embed_sparse) 假嵌入：hit=单位向量 / miss=正交向量。"""
    hit_vec = np.zeros(512, dtype=np.float32)
    hit_vec[0] = 1.0
    miss_vec = np.zeros(512, dtype=np.float32)
    miss_vec[1] = 1.0

    def _dense(texts):
        return [hit_vec if dim_hit else miss_vec]

    def _sparse(texts):
        return [{0: 1.0}]

    return _dense, _sparse


def test_tier1_passes_qdrant_score(seeded_kb, monkeypatch):
    monkeypatch.setattr(vector_store, "is_available", lambda: True)
    _dense, _sparse = _fake_embed(dim_hit=True)
    monkeypatch.setattr(embeddings, "embed_dense", _dense)
    monkeypatch.setattr(embeddings, "embed_sparse", _sparse)

    class _Point:
        score = 0.83

        def __init__(self):
            self.payload = {
                "id": 7,
                "domain": "教务",
                "keywords": "成绩,查询",
                "question": "成绩怎么查",
                "type": "index",
                "answer": "请登录教务系统查看。",
            }

    class _Resp:
        def __init__(self):
            self.points = [_Point()]

    class _Client:
        def query_points(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr(vector_store, "_get_client", lambda: _Client())

    hits = search_knowledge(seeded_kb, "成绩查询")
    assert hits and hits[0]["id"] == 7
    assert hits[0]["score"] == pytest.approx(0.83)


def test_tier2_passes_cosine_sim(seeded_kb, monkeypatch):
    monkeypatch.setattr(vector_store, "is_available", lambda: False)
    _dense, _sparse = _fake_embed(dim_hit=True)
    monkeypatch.setattr(embeddings, "embed_dense", _dense)

    with seeded_kb() as s, s.begin():
        for row in s.query(KnowledgeEntry).all():
            row.dense_vector = embeddings.dense_to_json(
                np.array(_dense(["x"])[0], dtype=np.float32)
            )

    hits = search_knowledge(seeded_kb, "成绩查询")
    assert hits and hits[0]["score"] == pytest.approx(1.0, abs=1e-6)


def test_tier2_low_sim_keeps_score_not_dropped(seeded_kb, monkeypatch):
    """正交向量 sim≈0 也带 score 字段（阈值过滤的输入前提）。

    阈值过滤在 search_knowledge 层；此处直接调 Tier2 函数验证分数本身贯穿。
    """
    monkeypatch.setattr(vector_store, "is_available", lambda: False)
    _dense, _sparse = _fake_embed(dim_hit=True)  # query 向量 = hit_vec
    monkeypatch.setattr(embeddings, "embed_dense", _dense)
    miss_vec = np.zeros(512, dtype=np.float32)
    miss_vec[1] = 1.0

    with seeded_kb() as s, s.begin():
        for row in s.query(KnowledgeEntry).all():
            row.dense_vector = embeddings.dense_to_json(miss_vec)  # 文档向量与之正交

    hits = _mysql_dense_search(seeded_kb, "成绩查询")
    assert hits and hits[0]["score"] == pytest.approx(0.0, abs=1e-6)


def test_tier3_passes_keyword_score(seeded_kb, monkeypatch):
    monkeypatch.setattr(vector_store, "is_available", lambda: False)

    def _raise(*_a, **_k):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_dense", _raise)

    # "成绩"2 分 + "查询"2 分 + 问题子串 1 分 = 5 分（≥ KEYWORD_MIN_SCORE 4）
    hits = search_knowledge(seeded_kb, "成绩怎么查询")
    assert hits and hits[0]["score"] == 5.0


def test_tier3_multi_keyword_score_accumulates(seeded_kb, monkeypatch):
    monkeypatch.setattr(vector_store, "is_available", lambda: False)

    def _raise(*_a, **_k):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_dense", _raise)

    hits = search_knowledge(seeded_kb, "成绩查询")  # "成绩"+"查询"双命中 = 4 分
    assert hits and hits[0]["score"] == 4.0
