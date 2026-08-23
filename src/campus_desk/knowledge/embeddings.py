"""向量化封装（M10）：fastembed 本地嵌入，提供稠密 + 稀疏两路。

- 稠密：BAAI/bge-small-zh-v1.5（512 维，中文语义）
- 稀疏：Qdrant/BM25（BM25 式，专名精确匹配）
进程内单例 + 懒加载；模型加载/下载失败时抛 EmbeddingUnavailable，由检索层降级。

fastembed 不是向量库（那是对 Qdrant 的误解）：它只负责"把文本变成向量"，
Qdrant 负责"存向量 + 检索"。二者上下游关系。
"""

from __future__ import annotations

import json

import numpy as np

from campus_desk.config import settings

_DENSE_MODEL = "BAAI/bge-small-zh-v1.5"
_SPARSE_MODEL = "Qdrant/BM25"
_DENSE_DIM = 512


class EmbeddingUnavailable(RuntimeError):
    """fastembed 未安装或模型加载/下载失败（离线/CI 无模型）。"""


# 模型懒加载缓存：成功缓存实例，失败缓存异常（避免离线/无网时每次重试验证下载超时）
_dense_cache: dict = {"model": None, "error": None}
_sparse_cache: dict = {"model": None, "error": None}


def _dense_model():
    if _dense_cache["error"] is not None:
        raise _dense_cache["error"]
    if _dense_cache["model"] is not None:
        return _dense_cache["model"]
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:  # 库没装 → 明确报错，不静默
        err = EmbeddingUnavailable("fastembed 未安装（pip install fastembed）")
        _dense_cache["error"] = err
        raise err from exc
    try:
        # bge 主源（HF）在本机网络不可达，改用 GCS 手动拉到本地的目录（specific_model_path 直接加载，绕开坏掉的 HF/GCS 兜底逻辑）
        if settings.bge_local_path:
            model = TextEmbedding(model_name=_DENSE_MODEL, specific_model_path=settings.bge_local_path)
        else:
            model = TextEmbedding(model_name=_DENSE_MODEL)
        _dense_cache["model"] = model
        return model
    except Exception as exc:  # 模型下载/加载失败（离线/无网络）  # noqa: BLE001
        err = EmbeddingUnavailable(f"稠密模型加载失败: {exc}")
        _dense_cache["error"] = err
        raise err


def _sparse_model():
    if _sparse_cache["error"] is not None:
        raise _sparse_cache["error"]
    if _sparse_cache["model"] is not None:
        return _sparse_cache["model"]
    try:
        from fastembed import SparseTextEmbedding
    except ImportError as exc:
        err = EmbeddingUnavailable("fastembed 未安装（pip install fastembed）")
        _sparse_cache["error"] = err
        raise err from exc
    try:
        # BM25 主源（HF）在本机网络不可达，改用手动拉取的本地快照目录（specific_model_path 直接加载）
        if settings.bm25_local_path:
            model = SparseTextEmbedding(model_name=_SPARSE_MODEL, specific_model_path=settings.bm25_local_path)
        else:
            model = SparseTextEmbedding(model_name=_SPARSE_MODEL)
        _sparse_cache["model"] = model
        return model
    except Exception as exc:  # noqa: BLE001
        err = EmbeddingUnavailable(f"稀疏模型加载失败: {exc}")
        _sparse_cache["error"] = err
        raise err


def embed_dense(texts: list[str]) -> np.ndarray:
    """返回 (N, 512) float32 数组；空列表返回 (0, 512)。"""
    if not texts:
        return np.zeros((0, _DENSE_DIM), dtype=np.float32)
    vecs = list(_dense_model().embed(texts))
    return np.asarray(vecs, dtype=np.float32)


def _segment_zh(text: str) -> str:
    """用 jieba 把中文切成词、空格拼接后返回。

    fastembed 的 BM25 按空白切 token，不切词会把整段无空格中文当成单一 token，
    导致跨句同义改写（如「饭卡丢了」vs「校园卡挂失」）零重叠、中文召回≈0。
    先 jieba 切词再喂进去 → 词级稀疏，IDF 仍走 Qdrant 原生集成。query 与 doc 两侧一致。
    """
    import jieba

    return " ".join(tok for tok in jieba.lcut(text or "") if tok.strip())


def embed_sparse(texts: list[str]) -> list[dict[int, float]]:
    """返回与 texts 等长的稀疏向量列表；每个是 {token_id: weight}。

    fastembed 0.8 的 SparseEmbedding 用 as_dict() 转 dict。
    中文须先 jieba 切词（见 _segment_zh），否则整段中文成单一 token、召回失效。
    """
    if not texts:
        return []
    seg_texts = [_segment_zh(t) for t in texts]
    out = []
    for sparse in _sparse_model().embed(seg_texts):
        # fastembed 0.8 的 SparseEmbedding 用 as_dict() 转 {token_id: weight}（非 to_dict）
        out.append(sparse.as_dict())
    return out


def dense_to_json(vec: np.ndarray) -> str:
    """稠密向量 → JSON 字符串（存 MySQL dense_vector 列）。"""
    return json.dumps(vec.tolist(), ensure_ascii=False)


def dense_from_json(s: str | None) -> np.ndarray | None:
    """MySQL dense_vector 列 → 向量；空/非法返回 None。"""
    if not s:
        return None
    try:
        return np.asarray(json.loads(s), dtype=np.float32)
    except (json.JSONDecodeError, ValueError):
        return None
