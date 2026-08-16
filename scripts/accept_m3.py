"""M3 全链路验收（进化闭环 6 步）：学生反馈/提议 → 管理页审查 → 补入知识库 → 再问命中。

环境：SQLite 内存库（种子）+ TestClient（Fake 意图恒 knowledge + InMemorySaver），
闭环路径全为确定性代码（反馈/审查/补入/检索均不调 LLM），无需 DEEPSEEK_API_KEY，稳定可跑。

用法：PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/accept_m3.py
"""

import sys

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from campus_desk.api.app import create_app
from campus_desk.api.graphs import GraphBundle, GraphRegistry
from campus_desk.db.base import Base
from campus_desk.db.models import BadCase, KnowledgeEntry, Suggestion
from campus_desk.db.seed import seed_all
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.intent import IntentResult
from campus_desk.knowledge.graph import build_knowledge_graph
from campus_desk.knowledge.search import search_knowledge
from campus_desk.query.graph import build_query_graph


class _FakeToolLLM:
    """query 图 stub：accept 闭环不经过 query 图，避免真 FC 构造依赖 key。"""

    def bind_tools(self, schemas):
        return self

    def invoke(self, messages):
        return type("FakeAIMessage", (), {"content": "", "tool_calls": []})()


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_all(factory)
    return factory


def _build_client(factory):
    class FakeClassifier:
        def classify(self, user_input):
            return IntentResult(intent="knowledge", confidence=0.9, secondary_intents=[], reason="t")

    def _bundle(user_id: str) -> GraphBundle:
        entry = build_entry_graph(classifier=FakeClassifier())
        knowledge = build_knowledge_graph(factory, checkpointer=InMemorySaver(), user_id=user_id)
        query = build_query_graph(
            factory, checkpointer=InMemorySaver(), llm=_FakeToolLLM(), user_id=user_id
        )
        return GraphBundle(entry=entry, knowledge=knowledge, query=query)

    registry = GraphRegistry(factory, bundle_factory=_bundle)
    app = create_app(session_factory=factory, registry=registry)
    return TestClient(app)


def _login(client, username, password="123456"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    return cond


def main() -> None:
    factory = _factory()
    client = _build_client(factory)
    student = _login(client, "student-001")
    admin = _login(client, "admin-001")
    results = []

    # 1. 学生手动反馈"没解决"（bad case 通道）
    r = client.post(
        "/api/feedback/bad-case",
        headers=student,
        json={"thread_id": "m3-1", "question": "研究生导师怎么选？", "reply": "超出知识范围", "note": "没解决"},
    )
    bid = r.json()["id"] if r.status_code == 200 else None
    results.append(check("1 学生手动反馈落 bad_cases", r.status_code == 200 and bid, f"id={bid}"))

    # 2. 学生提建议（suggestion 通道）
    r = client.post(
        "/api/feedback/suggestion",
        headers=student,
        json={"question": "校车时刻表在哪查？", "note": "希望补充交通信息"},
    )
    sid = r.json()["id"] if r.status_code == 200 else None
    results.append(check("2 学生提议落 suggestions", r.status_code == 200 and sid, f"id={sid}"))

    # 3. 管理员待审列表可见 + 预填建议
    r = client.get("/api/admin/reviews?kind=bad_cases", headers=admin)
    items = r.json().get("items", []) if r.status_code == 200 else []
    item = next((it for it in items if it["id"] == bid), None)
    results.append(
        check(
            "3 管理页待审列表 + 关键词预填",
            r.status_code == 200 and item is not None and "导师" in item["suggested_keywords"],
            f"待审={len(items)}",
        )
    )

    # 4. 管理员补入知识库（bad case 采纳）
    r = client.post(
        f"/api/admin/reviews/bad_cases/{bid}/adopt",
        headers=admin,
        json={
            "domain": "教务",
            "type": "info",
            "keywords": "导师,研究生,选导师",
            "answer": "研究生导师双向选择，名单与流程以学院官网通知为准。",
        },
    )
    with factory() as session:
        bc_status = session.get(BadCase, bid).status
        entry = session.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.question == "研究生导师怎么选？")
        ).scalar_one_or_none()
    results.append(
        check(
            "4 采纳补入知识库 + 状态流转",
            r.status_code == 200 and bc_status == "RESOLVED" and entry is not None,
            f"status={bc_status}",
        )
    )

    # 5. 闭环：补入后同问题检索命中（进化闭环成立证据）
    hits = search_knowledge(factory, "研究生导师怎么选？")
    results.append(
        check(
            "5 闭环：再问命中新知识",
            any(h["question"] == "研究生导师怎么选？" for h in hits),
            f"命中={len(hits)}",
        )
    )

    # 6. 管理员驳回建议（suggestions → REJECTED，不补入）
    r = client.post(f"/api/admin/reviews/suggestions/{sid}/dismiss", headers=admin)
    with factory() as session:
        sug_status = session.get(Suggestion, sid).status
    results.append(
        check(
            "6 驳回建议不补入",
            r.status_code == 200 and sug_status == "REJECTED",
            f"status={sug_status}",
        )
    )

    # 7. 权限门控：学生访问管理接口 403
    r = client.get("/api/admin/reviews?kind=bad_cases", headers=student)
    results.append(check("7 权限：student 访问管理页 403", r.status_code == 403, f"code={r.status_code}"))

    print(f"\n验收结果: {sum(results)}/7 通过")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
