"""S5 V2 检索评测指标单测（多 gold 纯函数）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from eval_metrics import (
    hit_at,
    mrr,
    ndcg_at,
    precision_at,
    recall_at,
    summarize,
)

IDS = [12, 13, 14, 15, 16]  # top5
GOLDS_HIT = [12, 15]        # 两个 gold：12 在 rank1、15 在 rank4
GOLDS_MISS = [99]           # 都不在


class TestRecall:
    def test_full_recall(self):
        assert recall_at(IDS, GOLDS_HIT, 5) == 1.0

    def test_partial_recall(self):
        assert recall_at(IDS, GOLDS_HIT, 3) == 0.5  # 只召回 12

    def test_miss(self):
        assert recall_at(IDS, GOLDS_MISS, 5) == 0.0

    def test_empty_golds(self):
        assert recall_at(IDS, [], 3) == 0.0


class TestHit:
    def test_hit3(self):
        assert hit_at(IDS, GOLDS_HIT, 3) == 1.0

    def test_hit5(self):
        assert hit_at(IDS, GOLDS_HIT, 5) == 1.0

    def test_miss(self):
        assert hit_at(IDS, GOLDS_MISS, 5) == 0.0


class TestMrr:
    def test_rank1(self):
        assert mrr([12, 13], [12]) == 1.0

    def test_rank2(self):
        assert mrr([13, 12], [12]) == pytest.approx(0.5)

    def test_rank4(self):
        assert mrr(IDS, GOLDS_HIT) == 1.0  # 第一个 gold 在 rank1

    def test_miss(self):
        assert mrr(IDS, GOLDS_MISS) == 0.0

    def test_second_gold_ignored(self):
        assert mrr([13, 12, 15], GOLDS_HIT) == 0.5  # 只算第一个 gold 排位


class TestNdcg:
    def test_perfect_order(self):
        # gold 12 在 rank1 召回，但 gold 15 未进 top3 → 排序未达理想（理想=2 个 gold 都前2）
        # dcg = 1/log2(2) = 1.0，idcg = 1/log2(2)+1/log2(3) ≈ 1.6309 → ndcg ≈ 0.613
        val = 1.0 / (1.0 + 1 / 1.58496)
        assert ndcg_at([12, 13, 14], GOLDS_HIT, 3) == pytest.approx(val, abs=1e-4)

    def test_gold_at_rank2(self):
        # gold 在 rank2：dcg = 1/log2(3)，ideal = 1/log2(2)
        val = (1 / 1.58496) / 1.0
        assert ndcg_at([13, 12, 14], [12], 3) == pytest.approx(val, abs=1e-4)

    def test_miss(self):
        assert ndcg_at(IDS, GOLDS_MISS, 3) == 0.0


class TestPrecision:
    def test_one_of_three(self):
        assert precision_at([12, 13, 14], GOLDS_HIT, 3) == pytest.approx(1 / 3)

    def test_two_of_five(self):
        assert precision_at(IDS, GOLDS_HIT, 5) == pytest.approx(0.4)

    def test_zero_k(self):
        assert precision_at(IDS, GOLDS_HIT, 0) == 0.0


class TestSummarize:
    def test_aggregation(self):
        results = [
            {"ids": [12, 13, 14], "golds": [12]},          # 完美：Recall@3=1, MRR=1, Hit=1
            {"ids": [13, 12, 14], "golds": [12, 15]},      # gold@rank2：MRR=0.5, Recall@3=0.5
            {"ids": [13, 14, 15], "golds": [99]},          # miss
        ]
        s = summarize(results, k_list=(1, 3))
        assert s["n"] == 3
        assert s["Recall@3"] == pytest.approx(round((1 + 0.5 + 0) / 3, 4))
        assert s["Hit@1"] == pytest.approx(round((1 + 0 + 0) / 3, 4))
        assert s["MRR"] == pytest.approx(round((1 + 0.5 + 0) / 3, 4))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
