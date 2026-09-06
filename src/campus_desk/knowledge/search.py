"""知识库检索层（M10-ZJUT）：Qdrant 混合检索 + MySQL 稠密向量兜底 + 关键词保底。

三档降级（均返回相同结构 list[dict]{id,domain,keywords,question,type,answer,score}；
score 为 M17A 新增的相关性分——Tier1 Qdrant RRF 分 / Tier2 余弦 sim / Tier3 关键词计分，
供阈值过滤（BUG-004）与校准脚本使用）：
- Tier1 Qdrant 混合（稠密‖稀疏 RRF）：需 fastembed + Qdrant 在线
- Tier2 MySQL 稠密向量 + numpy 余弦：需 fastembed（保语义，不退回纯关键词）
- Tier3 纯关键词计分：无嵌入依赖，最终保底

对外结构不变（设计 §4.5），KnowledgeGraph 与 retrieve 工具共用本层。
"""

from __future__ import annotations

import numpy as np

from campus_desk.config import settings
from campus_desk.db.models import KnowledgeEntry
from campus_desk.knowledge import embeddings, vector_store

_MAX_RESULTS = 3


def search_knowledge(session_factory, text: str, domain: str | None = None) -> list[dict]:
    """三档降级检索，返回结构一致。domain 可选，缩小范围（向后兼容，默认不过滤）。

    M17A（BUG-004）：各档先按 settings 相关性下限过滤再返回非空——滤空即落入
    下一档 / 最终走追问或转人工，不再"沾边硬答"。
    """
    # Tier1：Qdrant 混合检索
    if vector_store.is_available():
        try:
            hits = vector_store.hybrid_search(text, top_k=_MAX_RESULTS, domain=domain)
            hits = [h for h in hits if (h.get("score") or 0.0) >= settings.qdrant_min_score]
            if hits:
                return hits
        except Exception:  # noqa: BLE001, S110 — Qdrant 检索失败→降级，不应静默记录
            pass
    # Tier2：MySQL 稠密向量 + numpy 余弦（保语义，不退回纯关键词）
    try:
        hits = _mysql_dense_search(session_factory, text, domain)
        hits = [h for h in hits if (h.get("score") or 0.0) >= settings.dense_min_sim]
        if hits:
            return hits
    except embeddings.EmbeddingUnavailable:
        pass
    # Tier3：纯关键词保底（无嵌入依赖，最终兜底）
    return _keyword_search(session_factory, text, domain)


def _keyword_search(session_factory, text: str, domain: str | None = None) -> list[dict]:
    """原 M1 关键词计分（命中问题/关键词任一即得分，多关键词累计）。"""
    with session_factory() as session, session.begin():
        rows = session.query(KnowledgeEntry).all()
    scored = []
    for row in rows:
        if domain and row.domain != domain:
            continue
        score = 0
        for kw in row.keywords.split(","):
            if kw and kw in text:
                score += 2
        if row.question and row.question in text:
            score += 1
        if score >= settings.keyword_min_score:  # M17A：单关键词(2分)不再沾边入选
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [_row_to_dict(r, score=float(sc)) for sc, r in scored[:_MAX_RESULTS]]


def _mysql_dense_search(session_factory, text: str, domain: str | None = None) -> list[dict]:
    """MySQL 稠密向量 + numpy 余弦（Qdrant 不可用时的语义兜底，复用 S5 基线向量）。"""
    q = embeddings.embed_dense([text])[0]
    with session_factory() as session, session.begin():
        rows = session.query(KnowledgeEntry).all()
    scored = []
    for row in rows:
        dv = embeddings.dense_from_json(getattr(row, "dense_vector", None))
        if dv is None:
            continue
        if domain and row.domain != domain:
            continue
        sim = float(np.dot(q, dv) / (np.linalg.norm(q) * np.linalg.norm(dv) + 1e-9))
        scored.append((sim, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [_row_to_dict(r, score=float(sim)) for sim, r in scored[:_MAX_RESULTS]]


def _row_to_dict(r, score: float | None = None) -> dict:
    return {
        "id": r.id,
        "domain": r.domain,
        "keywords": r.keywords,
        "question": r.question,
        "type": r.type,
        "answer": r.answer,
        "score": score,
    }


def assemble_answer(hits: list[dict]) -> str:
    """按命中数组装（单条直接返回 answer / 多条按编号拼接列表）。

    type 分型（info/process/index）由条目的 answer 内嵌结构承载（设计 §4.5）。
    """
    if not hits:
        return ""
    if len(hits) == 1:
        return hits[0]["answer"]
    parts = [f"{i + 1}. {h['answer']}" for i, h in enumerate(hits)]
    return "为您找到以下相关信息：\n" + "\n".join(parts)
