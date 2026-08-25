"""检索评测指标（S5 V2）：多 gold 支持，纯函数无副作用。

指标：
- recall_at(ids, golds, k)：正确答案召回比例（多 gold 按比例）
- hit_at(ids, golds, k)：top-k 是否至少命中一个 gold（二值）
- mrr(ids, golds)：第一个 gold 的排位倒数（rank1=1.0，未命中=0）
- ndcg_at(ids, golds, k)：排序质量（二值相关性，越相关越靠前分越高）
- precision_at(ids, golds, k)：top-k 中 gold 占比（噪声）
"""

from __future__ import annotations

import math


def recall_at(ids: list[int], golds: list[int], k: int) -> float:
    """top-k 中命中的 gold 数 / gold 总数（0~1）。golds 为空返回 0。"""
    if not golds:
        return 0.0
    hit = len(set(ids[:k]) & set(golds))
    return hit / len(golds)


def hit_at(ids: list[int], golds: list[int], k: int) -> float:
    """top-k 是否至少命中一个 gold（二值 0/1）。"""
    return 1.0 if set(ids[:k]) & set(golds) else 0.0


def mrr(ids: list[int], golds: list[int]) -> float:
    """第一个 gold 的排位倒数。ids 用 1-based 排位，未命中返回 0。"""
    gold_set = set(golds)
    for rank, rid in enumerate(ids, start=1):
        if rid in gold_set:
            return 1.0 / rank
    return 0.0


def ndcg_at(ids: list[int], golds: list[int], k: int) -> float:
    """nDCG@k（二值相关性）：DCG = Σ rel_i / log2(i+1)，IDCG 用理想排序归一。"""
    gold_set = set(golds)
    dcg = sum(
        (1.0 / math.log2(i + 1))
        for i, rid in enumerate(ids[:k], start=1)
        if rid in gold_set
    )
    ideal = sum(
        1.0 / math.log2(i + 1)
        for i in range(1, min(len(golds), k) + 1)
    )
    return dcg / ideal if ideal > 0 else 0.0


def precision_at(ids: list[int], golds: list[int], k: int) -> float:
    """top-k 中 gold 占比（0~1），k 为 0 返回 0。"""
    if k <= 0:
        return 0.0
    return len(set(ids[:k]) & set(golds)) / k


def summarize(results: list[dict], k_list: tuple[int, ...] = (1, 3, 5)) -> dict:
    """批量汇总：results 为 [{"ids": [...], "golds": [...]}]，输出各指标均值。"""
    n = len(results)
    if n == 0:
        return {}
    out: dict = {"n": n}
    for k in k_list:
        out[f"Recall@{k}"] = round(sum(recall_at(r["ids"], r["golds"], k) for r in results) / n, 4)
        out[f"Hit@{k}"] = round(sum(hit_at(r["ids"], r["golds"], k) for r in results) / n, 4)
        out[f"nDCG@{k}"] = round(sum(ndcg_at(r["ids"], r["golds"], k) for r in results) / n, 4)
        out[f"P@{k}"] = round(sum(precision_at(r["ids"], r["golds"], k) for r in results) / n, 4)
    out["MRR"] = round(sum(mrr(r["ids"], r["golds"]) for r in results) / n, 4)
    return out
