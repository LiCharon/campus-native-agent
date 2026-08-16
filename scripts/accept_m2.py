"""M2 全链路验收（5 路径）：真 LLM 意图 + 真 FC + 确定性工具 + 追问 + 失败链熔断。

环境：SQLite 内存库（种子+mock）+ InMemorySaver 隔离 + 真 DeepSeek（无 key SKIP）。
路径 5（熔断转人工）用坏 factory 注入直连 query 图（失败链确定性，无需真工具失败）。

用法：PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/accept_m2.py
"""

import sys

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from campus_desk.config import settings
from campus_desk.db.base import Base
from campus_desk.db.seed import seed_all
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn
from campus_desk.knowledge.graph import build_knowledge_graph
from campus_desk.query.graph import build_query_graph


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_all(factory)
    return factory


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    return cond


def main() -> None:
    if not settings.deepseek_api_key:
        print("SKIP: 未配置 DEEPSEEK_API_KEY（.env 填写后重跑）")
        return

    factory = _factory()
    entry = build_entry_graph()
    kg = build_knowledge_graph(factory, checkpointer=InMemorySaver())
    qg = build_query_graph(factory, checkpointer=InMemorySaver())

    results = []

    # 路径 1：工具直查
    r = turn(entry, kg, qg, "acc-1", "3号楼下午有空教室吗？")
    results.append(
        check(
            "1 工具直查",
            r["route"] == "tool_query"
            and r["outcome"] == "answer"
            and r["tool_calls"] == ["query_empty_rooms"]
            and "空闲教室" in r["reply"],
            f"route={r['route']} outcome={r['outcome']} tool={r['tool_calls']}",
        )
    )

    # 路径 2：工具追问（缺楼栋 → 追问 → 补全后查）
    r1 = turn(entry, kg, qg, "acc-2", "有空教室吗？")
    r2 = turn(entry, kg, qg, "acc-2", "3号楼，下午")
    results.append(
        check(
            "2 工具追问",
            r1["outcome"] == "ask"
            and r2["outcome"] == "answer"
            and "query_empty_rooms" in r2["tool_calls"],
            f"首轮={r1['outcome']} 次轮={r2['outcome']}",
        )
    )

    # 路径 3：multi 主意图 tool_query + 次要提示
    r = turn(entry, kg, qg, "acc-3", "3号楼下午有空教室吗？顺便问下寒假")
    results.append(
        check(
            "3 multi 主意图 tool",
            r["route"] == "multi_intent"
            and "query_empty_rooms" in r["tool_calls"]
            and "可以继续问我" in r["reply"],
            f"route={r['route']}",
        )
    )

    # 路径 4：knowledge 回归
    r = turn(entry, kg, qg, "acc-4", "什么时候放寒假？")
    results.append(
        check(
            "4 knowledge 回归",
            r["route"] == "knowledge" and r["outcome"] == "answer" and "寒假" in r["reply"],
            f"outcome={r['outcome']}",
        )
    )

    # 路径 5：熔断转人工（坏 factory 注入直连 query 图：降级→降级→转人工+bad_cases）
    class BrokenFactory:
        def __init__(self, real):
            self.real = real
            self.n = 0

        def __call__(self):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("db down")
            return self.real()

    broken = build_query_graph(BrokenFactory(factory), checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "acc-5"}}
    a = broken.invoke({"user_input": "3号楼下午有空教室吗？"}, cfg)
    b = broken.invoke({"user_input": "再试一次"}, cfg)
    c = broken.invoke({"user_input": "再试一次"}, cfg)
    results.append(
        check(
            "5 熔断转人工",
            a["outcome"] == "degraded" and b["outcome"] == "degraded" and c["outcome"] == "handoff",
            f"降级={a['outcome']} 熔断={b['outcome']} 转人工={c['outcome']}",
        )
    )

    print(f"\n验收结果: {sum(results)}/5 通过")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
