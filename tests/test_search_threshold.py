"""M17A-T2/T3/T4：三档检索相关性阈值过滤（修 BUG-004）。

每档先按 settings 相关性下限过滤再返回非空；滤空则自然落入下一档 /
最终走追问或转人工——问库里没有的东西不再硬发沾边内容。
阈值为 settings 字段（初值保守），T8 校准后回写。
"""

from __future__ import annotations

import numpy as np
import pytest

from campus_desk.config import settings
from campus_desk.db.models import KnowledgeEntry
from campus_desk.knowledge import embeddings, vector_store
from campus_desk.knowledge.search import search_knowledge


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
        s.add(
            KnowledgeEntry(
                domain="图书馆",
                keywords="闭馆,时间",
                question="图书馆几点闭馆",
                type="info",
                answer="22:00 闭馆。",
            )
        )
    return db_session_factory


def _offline_tier3(monkeypatch):
    """强制走 Tier3：Qdrant 不可用 + 嵌入不可用。"""
    monkeypatch.setattr(vector_store, "is_available", lambda: False)

    def _raise(*_a, **_k):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_dense", _raise)


def test_t2_keyword_single_hit_filtered(seeded_kb, monkeypatch):
    """单关键词命中（2 分）< KEYWORD_MIN_SCORE(4) ⇒ 不再沾边硬答。"""
    _offline_tier3(monkeypatch)
    hits = search_knowledge(seeded_kb, "成绩")  # 只命中"成绩"一个关键词
    assert hits == []


def test_t2_keyword_double_hit_passes(seeded_kb, monkeypatch):
    _offline_tier3(monkeypatch)
    hits = search_knowledge(seeded_kb, "成绩查询")  # "成绩"+"查询"双命中 = 4 分
    assert len(hits) == 1 and hits[0]["question"] == "成绩怎么查"


def _vec_at_cosine(cos: float) -> np.ndarray:
    """构造与单位向量 e0 余弦为 cos 的单位向量：cos*e0 + sqrt(1-cos²)*e1。"""
    v = np.zeros(512, dtype=np.float32)
    v[0] = cos
    v[1] = np.sqrt(1 - cos * cos)
    return v


def test_t3_dense_below_sim_filtered(seeded_kb, monkeypatch):
    """稠密档 sim 0.1 < DENSE_MIN_SIM(0.35) 被滤。

    查询文本刻意不含任何种子关键词——稠密档滤空后关键词档也拿不到 4 分，
    整体返回 []（这正是 BUG-004 修复后"宁可不答"的目标行为）。
    """
    monkeypatch.setattr(vector_store, "is_available", lambda: False)
    hit_vec = np.zeros(512, dtype=np.float32)
    hit_vec[0] = 1.0
    monkeypatch.setattr(embeddings, "embed_dense", lambda texts: [hit_vec])

    with seeded_kb() as s, s.begin():
        for row in s.query(KnowledgeEntry).all():
            row.dense_vector = embeddings.dense_to_json(_vec_at_cosine(0.1))

    hits = search_knowledge(seeded_kb, "帮个忙呗")
    assert hits == []


def test_t3_dense_above_sim_kept(seeded_kb, monkeypatch):
    monkeypatch.setattr(vector_store, "is_available", lambda: False)
    hit_vec = np.zeros(512, dtype=np.float32)
    hit_vec[0] = 1.0
    monkeypatch.setattr(embeddings, "embed_dense", lambda texts: [hit_vec])

    with seeded_kb() as s, s.begin():
        rows = s.query(KnowledgeEntry).order_by(KnowledgeEntry.id).all()
        rows[0].dense_vector = embeddings.dense_to_json(hit_vec)  # sim 1.0
        rows[1].dense_vector = embeddings.dense_to_json(_vec_at_cosine(0.1))  # 滤

    hits = search_knowledge(seeded_kb, "成绩查询")
    assert [h["question"] for h in hits] == ["成绩怎么查"]


def test_t4_qdrant_score_filtered(seeded_kb, monkeypatch):
    """Qdrant 档：低于 qdrant_min_score 的 point 被滤掉。"""

    def _tier1_client(scores: list[float]):
        class _Point:
            def __init__(self, pid: int, score: float):
                self.score = score
                self.payload = {
                    "id": pid,
                    "domain": "教务",
                    "keywords": "成绩,查询",
                    "question": "成绩怎么查",
                    "type": "index",
                    "answer": "请登录教务系统查看。",
                }

        class _Resp:
            def __init__(self):
                self.points = [_Point(10 + i, s) for i, s in enumerate(scores)]

        class _Client:
            def query_points(self, *args, **kwargs):
                return _Resp()

        return _Client()

    _dense, _sparse = None, None
    hit_vec = np.zeros(512, dtype=np.float32)
    hit_vec[0] = 1.0
    monkeypatch.setattr(vector_store, "is_available", lambda: True)
    monkeypatch.setattr(embeddings, "embed_dense", lambda texts: [hit_vec])
    monkeypatch.setattr(embeddings, "embed_sparse", lambda texts: [{0: 1.0}])

    monkeypatch.setattr(settings, "qdrant_min_score", 0.2)
    monkeypatch.setattr(vector_store, "_get_client", lambda: _tier1_client([0.5, 0.05]))
    hits = search_knowledge(seeded_kb, "成绩查询")
    assert [h["id"] for h in hits] == [10]  # 0.05 被滤，0.5 保留

    monkeypatch.setattr(settings, "qdrant_min_score", 0.0)
    monkeypatch.setattr(vector_store, "_get_client", lambda: _tier1_client([0.5, 0.05]))
    hits = search_knowledge(seeded_kb, "成绩查询")
    assert [h["id"] for h in hits] == [10, 11]  # 阈值 0（初值）不过滤
