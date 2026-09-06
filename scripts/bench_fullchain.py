"""整条链路 + 放大梯度 benchmark（M10 检索延迟补全）。

与 s5_report.json 的区别：s5 只测了检索层（hybrid_search/_mysql_dense_search/_keyword_search
单次调用）。本脚本测 **整条对话链路**（意图分类 LLM → 检索 → 最终生成 LLM），并补齐
**数据放大梯度**（834 → 2000 → 4000 → 8000），看线性兜底路径(Tier2/3)随库增大的退化。

安全隔离（不污染生产库 campus_desk）：
- 启动即把 DATABASE_URL 整体切到 campus_desk_bench（克隆 schema+数据），usage 埋点等
  全局写入也只落 bench；生产库零写入。bench 库跑完可删。
- Qdrant 走磁盘模式（./bench_qdrant），免起服务；--no-qdrant 则强制走 MySQL 稠密主路径。

用法：
  python scripts/bench_fullchain.py                 # Tier1(Qdrant 磁盘) 一遍
  python scripts/bench_fullchain.py --no-qdrant     # Tier2(MySQL 稠密) 一遍
  python scripts/bench_fullchain.py --scales 834,2000,4000,8000
输出：
  bench_fullchain_report_<tier>.json / .csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT / "src"))

# ---- 1) 先解析 .env 拿生产 URL（不 import campus_desk，避免被覆盖）----
def _read_env(key: str) -> str | None:
    p = ROOT / ".env"
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}=") and not line.startswith(f"{key}_"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


PROD_URL = _read_env("DATABASE_URL")
if not PROD_URL:
    raise SystemExit("DATABASE_URL 未在 .env 找到")

# ---- 2) 切换 DATABASE_URL 到 bench（必须在 import campus_desk 之前）----
ap = argparse.ArgumentParser()
ap.add_argument("--no-qdrant", action="store_true", help="强制 Tier2 MySQL 稠密主路径")
ap.add_argument("--qdrant-url", default=str(ROOT / "bench_qdrant"), help="Qdrant 磁盘/远程 URL")
ap.add_argument("--bench-db", default="campus_desk_bench")
ap.add_argument("--scales", default="834,2000,4000,8000")
ap.add_argument("--fullchain-n", type=int, default=15)
ap.add_argument("--retrieval-n", type=int, default=60)
ap.add_argument("--warmup", type=int, default=20)
args = ap.parse_args()

BENCH_URL = PROD_URL.rsplit("/", 1)[0] + "/" + args.bench_db
os.environ["DATABASE_URL"] = BENCH_URL
if args.no_qdrant:
    os.environ["QDRANT_URL"] = ""
    TIER = "tier2"
else:
    os.environ["QDRANT_URL"] = args.qdrant_url
    TIER = "tier1"

# ---- 3) 现在 import 项目（settings 读到的是 bench）----
from campus_desk.config import settings  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

assert settings.database_url == BENCH_URL, (settings.database_url, BENCH_URL)
settings.qdrant_url = os.environ["QDRANT_URL"]


def _redact(u: str) -> str:
    return __import__("re").sub(r"://([^:]+):[^@]+@", r"://\1:***@", u)

from campus_desk.db.models import KnowledgeEntry  # noqa: E402
from campus_desk.knowledge import vector_store  # noqa: E402
from campus_desk.knowledge.search import (  # noqa: E402
    _keyword_search,
    _mysql_dense_search,
    search_knowledge,
)
from campus_desk.entry.entry_graph import build_entry_graph  # noqa: E402
from campus_desk.knowledge.graph import build_knowledge_graph  # noqa: E402
from campus_desk.query.graph import build_query_graph  # noqa: E402
from campus_desk.entry.orchestrator import turn as orchestrator_turn  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

import campus_desk.knowledge.vector_store as _vs  # noqa: E402

prod_factory = sessionmaker(bind=create_engine(PROD_URL, pool_pre_ping=True), expire_on_commit=False)
bench_factory = sessionmaker(bind=create_engine(BENCH_URL, pool_pre_ping=True), expire_on_commit=False)


# ---------- 工具 ----------
def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0}
    return {
        "n": len(vals),
        "mean": round(sum(vals) / len(vals) * 1000, 1),
        "p50": round(_pct(vals, 50) * 1000, 1),
        "p95": round(_pct(vals, 95) * 1000, 1),
        "p99": round(_pct(vals, 99) * 1000, 1),
        "min": round(min(vals) * 1000, 1),
        "max": round(max(vals) * 1000, 1),
    }


CLONE_BASE = 1_000_000
_SUFFIXES = ["（示例场景）", "（常见问法）", "的相关说明", "如何处理", "（咨询案例）"]


def _clone_prod_to_bench() -> None:
    """整库克隆 prod → bench（CREATE TABLE … LIKE + INSERT SELECT）。bench 可丢弃。"""
    with prod_factory() as s:
        tables = [r[0] for r in s.execute(text("SHOW TABLES")).all()]
    with prod_factory() as s:
        s.execute(text(f"CREATE DATABASE IF NOT EXISTS {args.bench_db}"))
        for t in tables:
            s.execute(text(f"DROP TABLE IF EXISTS {args.bench_db}.{t}"))
        for t in tables:
            s.execute(text(f"CREATE TABLE {args.bench_db}.{t} LIKE campus_desk.{t}"))
            s.execute(text(f"INSERT INTO {args.bench_db}.{t} SELECT * FROM campus_desk.{t}"))


def _reset_and_scale(target: int) -> int:
    """bench.knowledge_entries 重置为 base 834 + 插 (target-834) 克隆行（高位 id）。返回实际行数。"""
    with bench_factory() as s:
        s.execute(text(f"DELETE FROM knowledge_entries WHERE id >= {CLONE_BASE}"))
        base = s.query(KnowledgeEntry).all()
        n_base = len(base)
        need = max(0, target - n_base)
        for i in range(need):
            src = base[i % n_base]
            suf = _SUFFIXES[i % len(_SUFFIXES)]
            s.add(
                KnowledgeEntry(
                    id=CLONE_BASE + 1 + i,
                    domain=src.domain,
                    keywords=src.keywords,
                    question=f"{src.question}{suf}",
                    type=src.type,
                    answer=src.answer,
                    dense_vector=src.dense_vector,
                )
            )
        s.commit()  # 关键修复：克隆行必须显式提交，否则 with 退出只 close 不 commit，INSERT 被丢弃
    # 重建向量（Qdrant 可用时一并写；MySQL 稠密向量始终写）
    _vs._client = None
    _vs._avail_until = 0.0
    _vs._avail_flag = False
    res = vector_store.rebuild_all(bench_factory)
    with bench_factory() as s:
        actual = s.query(KnowledgeEntry).count()
    return actual, res


# ---------- 查询集（复用 s5 评测集）----------
def _load_queries() -> list[str]:
    p = SCRIPT_DIR / "s5_eval_set.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        return [c["query"] for c in data.get("cases", [])]
    # 兜底
    return ["图书馆开放时间", "校园卡挂失补办", "宿舍报修怎么弄", "选课在哪里操作", "校园网怎么连"]


def main() -> None:
    scales = [int(x) for x in args.scales.split(",") if x.strip()]
    queries = _load_queries()
    random.seed(1234)

    print(f"[setup] 克隆 prod→bench ({args.bench_db}) …", flush=True)
    _clone_prod_to_bench()
    with bench_factory() as s:
        base_n = s.query(KnowledgeEntry).count()
    print(f"[setup] bench 基线行数 = {base_n}", flush=True)

    # 构建图（bench session_factory + 临时 checkpointer）
    ckpt = SqliteSaver(sqlite3.connect(tempfile.mktemp(suffix=".db"), check_same_thread=False))
    entry_g = build_entry_graph()
    knowledge_g = build_knowledge_graph(bench_factory, checkpointer=ckpt, user_id="bench-user", profile="")
    query_g = build_query_graph(bench_factory, checkpointer=ckpt, user_id="bench-user", profile_text="")

    qdrant_on = vector_store.is_available()
    report = {
        "meta": {
            "baseline_rows": base_n,
            "scales": scales,
            "tier": TIER,
            "qdrant_on": qdrant_on,
            "qdrant_url": settings.qdrant_url,
            "database_url": _redact(BENCH_URL),
            "fullchain_n": args.fullchain_n,
            "retrieval_n": args.retrieval_n,
            "warmup": args.warmup,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "results": [],
    }

    for scale in scales:
        print(f"\n===== SCALE {scale} =====", flush=True)
        actual, rb = _reset_and_scale(scale)
        qdrant_on = vector_store.is_available()
        print(f"  实际行数={actual}  rebuild={rb}  qdrant_available={qdrant_on}", flush=True)

        # --- warmup ---
        for _ in range(args.warmup):
            orchestrator_turn(entry_g, knowledge_g, query_g, uuid.uuid4().hex,
                              random.choice(queries), user_id="bench-user", recent=[])
        for _ in range(args.warmup):
            search_knowledge(bench_factory, random.choice(queries))

        # --- 整链 ---
        fc_times: list[float] = []
        fc_routes: dict[str, int] = {}
        for _ in range(args.fullchain_n):
            msg = random.choice(queries)
            t0 = time.perf_counter()
            r = orchestrator_turn(entry_g, knowledge_g, query_g, uuid.uuid4().hex,
                                  msg, user_id="bench-user", recent=[])
            fc_times.append(time.perf_counter() - t0)
            rt = r.get("route", "?")
            fc_routes[rt] = fc_routes.get(rt, 0) + 1
        print(f"  整链 p50={ _stats(fc_times)['p50'] }ms p99={ _stats(fc_times)['p99'] }ms routes={fc_routes}", flush=True)

        # --- 检索各路径 ---
        def _measure(fn, label):
            ts: list[float] = []
            for _ in range(args.retrieval_n):
                t0 = time.perf_counter()
                fn(random.choice(queries))
                ts.append(time.perf_counter() - t0)
            st = _stats(ts)
            print(f"  {label:16} p50={st['p50']}ms p95={st['p95']}ms p99={st['p99']}ms", flush=True)
            return st

        cascade = _measure(lambda m: search_knowledge(bench_factory, m), "search_knowledge")
        tier1 = _measure(lambda m: vector_store.hybrid_search(m), "tier1_qdrant") if qdrant_on else None
        tier2 = _measure(lambda m: _mysql_dense_search(bench_factory, m), "tier2_mysql")
        tier3 = _measure(lambda m: _keyword_search(bench_factory, m), "tier3_keyword")

        report["results"].append({
            "scale": scale,
            "actual_rows": actual,
            "rebuild": rb,
            "fullchain": _stats(fc_times),
            "routes": fc_routes,
            "retrieval_cascade": cascade,
            "tier1_qdrant": tier1,
            "tier2_mysql": tier2,
            "tier3_keyword": tier3,
        })

    # --- 输出 ---
    out_json = SCRIPT_DIR / f"bench_fullchain_report_{TIER}.json"
    out_csv = SCRIPT_DIR / f"bench_fullchain_report_{TIER}.csv"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = [["scale", "metric", "n", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "min_ms", "max_ms"]]
    for r in report["results"]:
        for metric in ["fullchain", "retrieval_cascade", "tier1_qdrant", "tier2_mysql", "tier3_keyword"]:
            v = r.get(metric)
            if not v:
                continue
            rows.append([r["scale"], metric, v["n"], v["mean"], v["p50"], v["p95"], v["p99"], v["min"], v["max"]])
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)

    print(f"\n[done] 报告: {out_json.name} / {out_csv.name}", flush=True)
    print(json.dumps(report["meta"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
