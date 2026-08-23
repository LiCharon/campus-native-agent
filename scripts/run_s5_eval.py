"""M10 S5 检索评测：对 s5_eval_set.json 的每条 case 独立跑三档检索，算 Recall@3 + 延迟。

三档（与 search.py 完全一致的实现，独立调用以便横向对比）：
- Tier1 Qdrant 混合（稠密 bge ‖ 稀疏 jieba-BM25 → RRF）
- Tier2 MySQL 稠密向量 + numpy 余弦（语义腿）
- Tier3 纯关键词计分（无嵌入依赖）

目的：在 262 条规模下用数字呈现「暴力（Tier2）≈ Qdrant（Tier1）」的选型边界，
并验证中文混合检索（稀疏腿经 jieba 注入后）真实生效。

用法（需先 rebuild_qdrant 灌好向量）：
  BGE_LOCAL_PATH=C:/models/bge-zh-v1.5/fast-bge-small-zh-v1.5 \\
  PYTHONPATH=src python scripts/run_s5_eval.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from campus_desk.config import settings
from campus_desk.db.session import default_session_factory
from campus_desk.knowledge import search as search_layer
from campus_desk.knowledge import vector_store

QDRANT_LOCAL = os.environ.get("QDRANT_LOCAL", "C:/campusdesk_qdrant_data")
EVAL_SET = ROOT / "scripts" / "s5_eval_set.json"
TOP_K = 3


def _hit(recall_ids: list[int], expected: int) -> bool:
    return expected in recall_ids


def main() -> None:
    # Tier1 需要 Qdrant 本地磁盘库；Tier2/3 只需 MySQL + 稠密向量。
    # bge/BM25 本地路径由 pydantic 从 .env 读入 settings（不写 os.environ，勿用 os.environ 覆盖）。
    settings.qdrant_url = QDRANT_LOCAL

    factory = default_session_factory()
    with open(EVAL_SET, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    tier1_on = vector_store.is_available()
    print(f"Qdrant 可用(Tier1): {tier1_on}  |  评测集: {len(cases)} 条\n")

    stats = {
        "Tier1_Qdrant混合": {"total": 0, "hit": 0, "lat": []},
        "Tier2_MySQL稠密": {"total": 0, "hit": 0, "lat": []},
        "Tier3_关键词": {"total": 0, "hit": 0, "lat": []},
    }
    by_cat = {}  # category -> {tier: [hit_bool...]}

    per_case = []
    for c in cases:
        q, exp, cat = c["query"], c["expected_id"], c["category"]
        by_cat.setdefault(cat, {t: [] for t in stats})

        # Tier1
        if tier1_on:
            t0 = time.perf_counter()
            try:
                h1 = vector_store.hybrid_search(q, top_k=TOP_K)
            except Exception as e:  # noqa: BLE001
                h1 = []
                print(f"  [Tier1 ERR] {q!r}: {e}")
            lat1 = (time.perf_counter() - t0) * 1000
            ids1 = [h["id"] for h in h1]
            hit1 = _hit(ids1, exp)
            stats["Tier1_Qdrant混合"]["total"] += 1
            stats["Tier1_Qdrant混合"]["hit"] += int(hit1)
            stats["Tier1_Qdrant混合"]["lat"].append(lat1)
            by_cat[cat]["Tier1_Qdrant混合"].append(hit1)
        else:
            hit1, ids1, lat1 = None, [], None

        # Tier2
        t0 = time.perf_counter()
        h2 = search_layer._mysql_dense_search(factory, q, None)
        lat2 = (time.perf_counter() - t0) * 1000
        ids2 = [h["id"] for h in h2]
        hit2 = _hit(ids2, exp)
        stats["Tier2_MySQL稠密"]["total"] += 1
        stats["Tier2_MySQL稠密"]["hit"] += int(hit2)
        stats["Tier2_MySQL稠密"]["lat"].append(lat2)
        by_cat[cat]["Tier2_MySQL稠密"].append(hit2)

        # Tier3
        t0 = time.perf_counter()
        h3 = search_layer._keyword_search(factory, q, None)
        lat3 = (time.perf_counter() - t0) * 1000
        ids3 = [h["id"] for h in h3]
        hit3 = _hit(ids3, exp)
        stats["Tier3_关键词"]["total"] += 1
        stats["Tier3_关键词"]["hit"] += int(hit3)
        stats["Tier3_关键词"]["lat"].append(lat3)
        by_cat[cat]["Tier3_关键词"].append(hit3)

        per_case.append({
            "query": q, "expected_id": exp, "category": cat,
            "tier1_ids": ids1, "tier1_hit": hit1, "tier1_lat_ms": round(lat1, 1) if lat1 is not None else None,
            "tier2_ids": ids2, "tier2_hit": hit2, "tier2_lat_ms": round(lat2, 1),
            "tier3_ids": ids3, "tier3_hit": hit3, "tier3_lat_ms": round(lat3, 1),
        })
        # 仅打印未命中 Tier1 的 case，便于排查
        if tier1_on and not hit1:
            print(f"  [Tier1 MISS] cat={cat} exp={exp} q={q!r} -> top3={ids1}")

    # 汇总
    print("\n=== 总体 Recall@3 + 平均延迟（ms） ===")
    for tier, s in stats.items():
        if s["total"] == 0:
            continue
        rec = s["hit"] / s["total"] * 100
        avg_lat = sum(s["lat"]) / len(s["lat"]) if s["lat"] else 0
        print(f"  {tier:18s}  Recall@{TOP_K} = {rec:5.1f}%  ({s['hit']}/{s['total']})   平均延迟 {avg_lat:6.2f} ms")

    print("\n=== 按类别 Recall@3（Tier1 / Tier2 / Tier3） ===")
    for cat, d in by_cat.items():
        parts = []
        for tier in stats:
            arr = d.get(tier, [])
            if not arr:
                continue
            r = sum(arr) / len(arr) * 100
            parts.append(f"{tier.split('_')[1] if '_' in tier else tier}:{r:4.0f}%")
        print(f"  {cat:14s}  " + "  ".join(parts))

    # 持久化
    out = {"summary": stats, "by_category": by_cat, "per_case": per_case}
    with open(ROOT / "scripts" / "s5_report.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n报告已写 scripts/s5_report.json")


if __name__ == "__main__":
    main()
