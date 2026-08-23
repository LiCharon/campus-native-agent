"""Qdrant 向量库封装（M10）：稠密 + 稀疏混合检索，连不上不抛错。

职责边界：
- is_available()：轻量健康检查（缓存 30s），供检索层决定是否走 Qdrant
- hybrid_search()：prefetch 稠密 ‖ 稀疏 → RRF 融合，返回与 search_knowledge 同结构
- rebuild_all()：读全表 → fastembed 向量化 → 写 Qdrant（可用时）+ 写 MySQL 稠密向量（始终）
- 不带 Qdrant（未配置/挂了）时全部走 MySQL 兜底（search.py 的 Tier2/3）

QDRANT_URL 形态：
- 空串 → 不启用（is_available 永 False，系统用 MySQL 兜底）
- http(s)://... → 连远程/容器 Qdrant
- 本地路径 → QdrantClient(path=...) 磁盘模式（无需起服务，便于本地试跑）
"""

from __future__ import annotations

import time

from campus_desk.config import settings

_COLLECTION = "knowledge"
_DENSE_DIM = 512
_SPARSE_NAME = "sparse"
_CACHE_TTL = 30.0

_client = None
_avail_until = 0.0
_avail_flag = False


def _get_client():
    """懒加载 Qdrant 客户端；未配置或导入失败返回 None。"""
    global _client
    if _client is not None:
        return _client
    if not settings.qdrant_url:
        return None
    from qdrant_client import QdrantClient

    url = settings.qdrant_url
    if url.startswith("http"):
        _client = QdrantClient(url=url, timeout=3.0)
    else:
        _client = QdrantClient(path=url)  # 本地磁盘模式
    return _client


def is_available() -> bool:
    """Qdrant 是否可用（缓存 30s，任何异常都视为不可用，不向外抛）。"""
    global _avail_until, _avail_flag
    now = time.time()
    if now < _avail_until:
        return _avail_flag
    try:
        client = _get_client()
    except Exception:  # noqa: BLE001 — 连接失败=不可用，不向外抛
        _avail_flag, _avail_until = False, now + _CACHE_TTL
        return False
    if client is None:
        _avail_flag, _avail_until = False, now + _CACHE_TTL
        return False
    try:
        client.get_collections()
        _avail_flag, _avail_until = True, now + _CACHE_TTL
    except Exception:  # noqa: BLE001 — 健康检查失败=不可用
        _avail_flag, _avail_until = False, now + _CACHE_TTL
    return _avail_flag


def ensure_collection() -> None:
    """建集合（若没有）：稠密 512 余弦 + 稀疏 BM25 双向量。"""
    client = _get_client()
    if client is None:
        raise RuntimeError("QDRANT_URL 未配置，无法建集合")
    from qdrant_client.models import Distance, SparseVectorParams, VectorParams

    if not client.collection_exists(_COLLECTION):
        client.create_collection(
            collection_name=_COLLECTION,
            vectors_config={
                "dense": VectorParams(size=_DENSE_DIM, distance=Distance.COSINE)
            },
            sparse_vectors_config={_SPARSE_NAME: SparseVectorParams()},
        )


def hybrid_search(text: str, top_k: int = 3, domain: str | None = None) -> list[dict]:
    """Qdrant 混合检索：稠密 ‖ 稀疏 prefetch → RRF。返回同 search_knowledge 结构。

    调用方（search.py）已确认 is_available()；此处连接/检索失败原样抛出，
    由检索层降级到 MySQL/关键词。
    """
    client = _get_client()
    if client is None:
        raise RuntimeError("QDRANT_URL 未配置")

    from qdrant_client.models import (
        FieldCondition,
        Filter,
        Fusion,
        FusionQuery,
        MatchValue,
        Prefetch,
        SparseVector,
    )

    from campus_desk.knowledge import embeddings

    dense = embeddings.embed_dense([text])[0].tolist()
    sparse = embeddings.embed_sparse([text])[0]
    prefetch = [
        Prefetch(query=dense, using="dense", limit=top_k * 4),
        Prefetch(
            query=SparseVector(indices=list(sparse.keys()), values=list(sparse.values())),
            using=_SPARSE_NAME,
            limit=top_k * 4,
        ),
    ]
    query_filter = None
    if domain:
        query_filter = Filter(
            must=[FieldCondition(key="domain", match=MatchValue(value=domain))]
        )
    resp = client.query_points(
        collection_name=_COLLECTION,
        query=FusionQuery(fusion=Fusion.RRF),
        prefetch=prefetch,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    )
    return [
        {
            "id": p.payload["id"],
            "domain": p.payload.get("domain", ""),
            "keywords": p.payload.get("keywords", ""),
            "question": p.payload.get("question", ""),
            "type": p.payload.get("type", "info"),
            "answer": p.payload.get("answer", ""),
        }
        for p in resp.points
    ]


def rebuild_all(session_factory) -> dict:
    """读全表 → fastembed 向量化 → 写 Qdrant（可用时）+ 写 MySQL 稠密向量（始终）。

    幂等：重复运行覆盖。返回 {upserted, qdrant} 供脚本打印。
    """
    from qdrant_client.models import PointStruct, SparseVector

    from campus_desk.db.models import KnowledgeEntry
    from campus_desk.knowledge import embeddings

    with session_factory() as session, session.begin():
        rows = session.query(KnowledgeEntry).all()
        entries = [
            {
                "id": r.id,
                "domain": r.domain,
                "keywords": r.keywords,
                "question": r.question,
                "type": r.type,
                "answer": r.answer,
            }
            for r in rows
        ]
    if not entries:
        return {"upserted": 0, "qdrant": False}

    # 文本 = 问题 + 关键词（提升召回），统一嵌入
    texts = [f"{e['question']} {' '.join(e['keywords'].split(','))}" for e in entries]
    dense = embeddings.embed_dense(texts)
    sparse = embeddings.embed_sparse(texts)

    # 始终写 MySQL 稠密向量（兜底语义检索 + S5 基线数据源）
    with session_factory() as session, session.begin():
        for e, dv in zip(entries, dense):
            row = session.get(KnowledgeEntry, e["id"])
            if row is not None:
                row.dense_vector = embeddings.dense_to_json(dv)

    q_ok = False
    if is_available():
        ensure_collection()
        client = _get_client()
        points = [
            PointStruct(
                id=e["id"],
                vector={
                    "dense": dv.tolist(),
                    _SPARSE_NAME: SparseVector(
                        indices=list(sp.keys()), values=list(sp.values())
                    ),
                },
                payload=e,
            )
            for e, dv, sp in zip(entries, dense, sparse)
        ]
        client.upsert(collection_name=_COLLECTION, points=points)
        q_ok = True
    return {"upserted": len(entries), "qdrant": q_ok}
