"""M17A-T8：三档检索相关性阈值校准（BUG-004 配套，长期保留资产）。

方法（grilling 拍板 2026-09-07）：
- 召回侧 = s5_eval_set.json 70 条（gold_ids 多值）；假阳性侧 = 库外边界探测集
  （含前端截图实证的"怎么申请教室借用""怎么申请出国读博"）。
- 每条查询每档只检索一次（阈值取 0 的原始 top3+分数），网格在缓存结果上过滤——
  与在线行为等价（分数单调 → top3 内过滤 == 过滤后取 top3）。
- 产出清单式报告：各候选阈值下 Recall@3 + 召回损失明细 + 假阳性明细。
- 抉择程序：先找"召回不掉、假阳性清零"双满足点 → 不存在则假阳性归零为硬门，
  召回损失清单交用户逐条复核后定线，实测值回写 config.py。

用法（对齐 run_s5_eval）：
  PYTHONPATH=src python scripts/calibrate_threshold.py [--qdrant 本地路径]
输出：scripts/calibrate_report.json + 控制台摘要
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from campus_desk.config import settings
from campus_desk.db.session import default_session_factory
from campus_desk.knowledge import search as search_layer
from campus_desk.knowledge import vector_store

EVAL_SET = ROOT / "scripts" / "s5_eval_set.json"
REPORT = ROOT / "scripts" / "calibrate_report.json"

# 库外/边界探测集（假阳性侧）：库内没有的问题，任何阈值下都不应发出内容
PROBES = [
    ("怎么申请教室借用", "前端截图实证 BUG-004 案例（2026-09-07）"),
    ("怎么申请出国读博", "M17A_PLAN 验收冒烟用例"),
    ("量子力学怎么学", "课程学习类，非办事咨询"),
    ("怎么办护照去旅游", "社会事务，非校园服务"),
]

DENSE_GRID = [0.0, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
KEYWORD_GRID = [0, 2, 3, 4, 5, 6]
QDRANT_GRID = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50]


def _golds(case: dict) -> list[int]:
    if case.get("gold_ids"):
        return [int(x) for x in case["gold_ids"]]
    if case.get("expected_id") is not None:
        return [int(case["expected_id"])]
    return []


def _collect(factory, tier1_on: bool) -> tuple[list[dict], list[dict]]:
    """每条查询每档检索一次（阈值置 0 拿原始 top3+分数）。"""
    with open(EVAL_SET, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    old_kw = settings.keyword_min_score
    settings.keyword_min_score = 0  # 原始收集：只留 score>0 的计分逻辑
    recalls, probes = [], []
    try:
        for c in cases:
            item = {"query": c["query"], "golds": _golds(c), "category": c["category"]}
            if tier1_on:
                item["tier1"] = vector_store.hybrid_search(c["query"], top_k=3)
            item["tier2"] = search_layer._mysql_dense_search(factory, c["query"], None)
            # 关键词档原始收集：min_score=0 会放进 0 分行（挤占 top3 尾位），按旧 >0 口径剔除
            item["tier3"] = [
                h
                for h in search_layer._keyword_search(factory, c["query"], None)
                if (h.get("score") or 0.0) > 0
            ]
            recalls.append(item)
        for q, note in PROBES:
            item = {"query": q, "note": note}
            if tier1_on:
                item["tier1"] = vector_store.hybrid_search(q, top_k=3)
            item["tier2"] = search_layer._mysql_dense_search(factory, q, None)
            item["tier3"] = [
                h
                for h in search_layer._keyword_search(factory, q, None)
                if (h.get("score") or 0.0) > 0
            ]
            probes.append(item)
    finally:
        settings.keyword_min_score = old_kw
    return recalls, probes


def _scan(recalls: list[dict], probes: list[dict], tier: str, grid: list) -> list[dict]:
    """单档网格扫描：Recall@3 + 召回损失/假阳性明细。"""
    rows = []
    for th in grid:

        def kept_ids(hits: list[dict], _th: float = th) -> set[int]:
            return {h["id"] for h in hits if (h.get("score") or 0.0) >= _th}

        hit = sum(1 for r in recalls if kept_ids(r[tier]) & set(r["golds"]))
        lost = [
            {
                "query": r["query"],
                "golds": r["golds"],
                "top3": [(h["id"], round(h.get("score") or 0.0, 4)) for h in r[tier]],
            }
            for r in recalls
            if r["golds"] and not kept_ids(r[tier]) & set(r["golds"])
        ]
        fps = [
            {
                "query": p["query"],
                "kept": [
                    (h["id"], round(h.get("score") or 0.0, 4))
                    for h in p[tier]
                    if (h.get("score") or 0.0) >= th
                ],
            }
            for p in probes
            if kept_ids(p[tier])
        ]
        rows.append({
            "threshold": th,
            "recall@3": round(hit / len(recalls), 4),
            "recall_lost_n": len(lost),
            "probe_fp_n": len(fps),
            "lost_detail": lost,
            "fp_detail": fps,
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant", default=None, help="Qdrant 本地磁盘路径（不传走 .env/不启用）")
    args = ap.parse_args()
    if args.qdrant:
        settings.qdrant_url = args.qdrant

    factory = default_session_factory()
    tier1_on = vector_store.is_available()
    print(f"Qdrant 可用(Tier1): {tier1_on} | 库外探测 {len(PROBES)} 条\n")

    recalls, probes = _collect(factory, tier1_on)

    report = {
        "meta": {
            "eval_set": str(EVAL_SET),
            "n_recall": len(recalls),
            "probes": [{"query": q, "note": n} for q, n in PROBES],
            "tier1_on": tier1_on,
        },
        "tier2_dense": _scan(recalls, probes, "tier2", DENSE_GRID),
        "tier3_keyword": _scan(recalls, probes, "tier3", KEYWORD_GRID),
    }
    if tier1_on:
        report["tier1_qdrant"] = _scan(recalls, probes, "tier1", QDRANT_GRID)

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    # 控制台摘要：双满足点（假阳性 0 且召回不掉）优先标出
    for tier_name, rows in report.items():
        if not isinstance(rows, list):
            continue
        base = rows[0]["recall@3"]
        print(f"\n=== {tier_name}（无阈值基线 R@3={base}） ===")
        print(f"{'阈值':>6} {'R@3':>7} {'召回损失':>6} {'假阳性':>6}  双满足")
        for r in rows:
            dual = "✅" if r["probe_fp_n"] == 0 and r["recall@3"] >= base else ""
            print(
                f"{r['threshold']:>6} {r['recall@3']:>7} {r['recall_lost_n']:>6}"
                f" {r['probe_fp_n']:>6}  {dual}"
            )
    print(f"\n明细（损失/假阳性清单）→ {REPORT}")


if __name__ == "__main__":
    main()
