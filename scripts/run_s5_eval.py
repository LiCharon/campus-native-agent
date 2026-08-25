"""M10/M11 S5 检索评测（V2）：对 s5_eval_set.json 每条 case 独立跑三档检索，
算全套指标（Recall@k / Hit@k / nDCG@k / P@k / MRR）+ 延迟，按类别切分。

三档（与 search.py 完全一致的实现，独立调用以便横向对比）：
- Tier1 Qdrant 混合（稠密 bge ‖ 稀疏 jieba-BM25 → RRF）
- Tier2 MySQL 稠密向量 + numpy 余弦（语义腿）
- Tier3 纯关键词计分（无嵌入依赖）

评测集格式（V2）：cases 支持 gold_ids（多值 1-3 个）；兼容旧单值 expected_id。

用法（需先 rebuild_qdrant 灌好向量）：
  PYTHONPATH=src python scripts/run_s5_eval.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from eval_metrics import summarize

from campus_desk.config import settings
from campus_desk.db.session import default_session_factory
from campus_desk.knowledge import search as search_layer
from campus_desk.knowledge import vector_store

QDRANT_LOCAL = os.environ.get("QDRANT_LOCAL", "C:/campusdesk_qdrant_data")
EVAL_SET = ROOT / "scripts" / "s5_eval_set.json"
TOP_K = 5  # 拉取 top5，指标按 k=1/3/5 分别算


def _golds(case: dict) -> list[int]:
    """兼容 V1 单值 expected_id 与 V2 多值 gold_ids。"""
    if case.get("gold_ids"):
        return [int(x) for x in case["gold_ids"]]
    if case.get("expected_id") is not None:
        return [int(case["expected_id"])]
    return []


def _run_tier(name: str, fn, case: dict, tier_on: bool, stats, by_cat, per_case, key: str):
    """跑单个 tier：fn(query) -> list[{id}]，更新 stats/by_cat/per_case。"""
    cat = case["category"]
    golds = _golds(case)
    if not tier_on:
        per_case.append({f"{key}_ids": [], f"{key}_result": None})
        return
    t0 = time.perf_counter()
    try:
        hits = fn(case["query"])
    except Exception as e:  # noqa: BLE001
        hits = []
        print(f"  [{key} ERR] {case['query']!r}: {e}")
    lat = (time.perf_counter() - t0) * 1000
    ids = [h["id"] for h in hits]
    stats[name]["result"].append({"ids": ids, "golds": golds})
    stats[name]["lat"].append(lat)
    by_cat[cat][name].append({"ids": ids, "golds": golds})
    per_case.append({f"{key}_ids": ids, f"{key}_lat_ms": round(lat, 1)})


def main() -> None:
    settings.qdrant_url = QDRANT_LOCAL
    factory = default_session_factory()
    with open(EVAL_SET, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    tier1_on = vector_store.is_available()
    print(f"Qdrant 可用(Tier1): {tier1_on}  |  评测集: {len(cases)} 条（多 gold）\n")

    stats = {
        "Tier1_Qdrant混合": {"result": [], "lat": []},
        "Tier2_MySQL稠密": {"result": [], "lat": []},
        "Tier3_关键词": {"result": [], "lat": []},
    }
    by_cat = defaultdict(lambda: defaultdict(list))
    per_case = []

    for c in cases:
        if tier1_on:
            _run_tier("Tier1_Qdrant混合", lambda q: vector_store.hybrid_search(q, top_k=TOP_K), c,
                      True, stats, by_cat, per_case, "tier1")
        else:
            per_case.append({"tier1_ids": [], "tier1_hit": None})
        _run_tier("Tier2_MySQL稠密", lambda q: search_layer._mysql_dense_search(factory, q, None), c,
                  True, stats, by_cat, per_case, "tier2")
        _run_tier("Tier3_关键词", lambda q: search_layer._keyword_search(factory, q, None), c,
                  True, stats, by_cat, per_case, "tier3")
        # MISS 排查：Tier1 在 Recall@3 全漏的 case
        if tier1_on:
            ids1 = per_case[-3]["tier1_ids"] if len(per_case) >= 3 else []
            if not set(ids1[:3]) & set(_golds(c)):
                print(f"  [Tier1 MISS] cat={c['category']} golds={_golds(c)} q={c['query']!r} -> top3={ids1[:3]}")

    # 总体
    print("\n=== 总体（全套指标，三档对比） ===")
    header = f"{'tier':18s} " + " ".join(
        f"{m:>8s}" for m in ("R@1", "R@3", "R@5", "H@1", "MRR", "nD@3", "P@3", "lat_avg", "p50", "p99")
    )
    print(header)
    all_out = {}
    for tier, s in stats.items():
        if not s["result"]:
            continue
        sm = summarize(s["result"], k_list=(1, 3, 5))
        lat = np.asarray(s["lat"]) if s["lat"] else np.zeros(1)
        lat_stats = {
            "lat_avg": round(float(lat.mean()), 1),
            "p50": round(float(np.percentile(lat, 50)), 1),
            "p99": round(float(np.percentile(lat, 99)), 1),
        }
        row = {
            "R@1": sm["Recall@1"], "R@3": sm["Recall@3"], "R@5": sm["Recall@5"],
            "H@1": sm["Hit@1"], "MRR": sm["MRR"], "nD@3": sm["nDCG@3"], "P@3": sm["P@3"],
            **lat_stats,
        }
        all_out[tier] = row
        print(f"{tier:18s} " + " ".join(f"{row[m]:>8}" for m in
              ("R@1", "R@3", "R@5", "H@1", "MRR", "nD@3", "P@3", "lat_avg", "p50", "p99")))

    # 按类别
    print("\n=== 按类别 Recall@3 / Hit@1 / MRR（T1/T2/T3） ===")
    cat_out = {}
    for cat, d in by_cat.items():
        parts = []
        cat_out[cat] = {}
        for tier in stats:
            arr = d.get(tier, [])
            if not arr:
                continue
            sm = summarize(arr, k_list=(1, 3))
            cat_out[cat][tier] = {"R@3": sm["Recall@3"], "H@1": sm["Hit@1"], "MRR": sm["MRR"]}
            short = tier.replace("Tier1_Qdrant混合", "T1").replace("Tier2_MySQL稠密", "T2").replace("Tier3_关键词", "T3")
            parts.append(
                f"{short} R3:{sm['Recall@3']*100:.0f}% H1:{sm['Hit@1']*100:.0f}% MRR:{sm['MRR']:.2f}"
            )
        print(f"  {cat:14s}  " + "  ".join(parts))

    # 持久化
    out = {"meta": {"n": len(cases), "k": TOP_K, "metrics": ["Recall@1/3/5", "Hit@1", "MRR", "nDCG@3", "P@3", "lat"]},
           "summary": all_out, "by_category": cat_out, "per_case": per_case}
    with open(ROOT / "scripts" / "s5_report.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n报告已写 scripts/s5_report.json")


if __name__ == "__main__":
    main()
