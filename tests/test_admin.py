"""管理页审查 API 测试（M3）：待审列表 + 补入知识库/驳回 + 闭环命中。

覆盖（设计 §5.5 管理员审查）：
- 待审列表：admin 可见（含 suggested_keywords 预填建议），空列表 []
- 权限：student 403 / 未登录 401 / 非法 kind 422
- adopt（bad_cases → RESOLVED + 补入 knowledge_entries；suggestions → ADOPTED）
- dismiss（bad_cases → RESOLVED；suggestions → REJECTED，不产生知识条目）
- 已处理重复操作 404；未知 id 404
- 字段校验：domain/type 枚举 422、keywords/answer 空白 422
- 闭环证据：adopt 后 search_knowledge 命中新条目
"""

from sqlalchemy import select

from campus_desk.db.models import BadCase, KnowledgeEntry, Suggestion
from campus_desk.knowledge.search import search_knowledge

_DOMAINS = ["教务", "后勤", "图书馆", "IT", "证件", "生活"]


def _login(client, username, password="123456"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed_bad_case(api_client, *, question="研究生导师怎么选？"):
    headers = _login(api_client, "student-001")
    r = api_client.post(
        "/api/feedback/bad-case",
        headers=headers,
        json={"thread_id": "t-x", "question": question, "reply": "超出知识范围", "note": "没解决"},
    )
    assert r.status_code == 200
    return r.json()["id"]


def _seed_suggestion(api_client, *, question="校车时刻表在哪查？"):
    headers = _login(api_client, "student-001")
    r = api_client.post(
        "/api/feedback/suggestion",
        headers=headers,
        json={"question": question, "note": "希望能有答案"},
    )
    assert r.status_code == 200
    return r.json()["id"]


def _adopt(api_client, kind, rid, **over):
    payload = {
        "domain": "教务",
        "type": "info",
        "keywords": "导师,研究生,选导师",
        "answer": "研究生导师双向选择，流程与名单以学院官网通知为准。",
        **over,
    }
    return api_client.post(
        f"/api/admin/reviews/{kind}/{rid}/adopt",
        headers=_login(api_client, "admin-001"),
        json=payload,
    )


def test_admin_list_bad_cases_with_suggested_keywords(api_client, db_session_factory):
    bid = _seed_bad_case(api_client)
    headers = _login(api_client, "admin-001")
    r = api_client.get("/api/admin/reviews?kind=bad_cases", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    item = next(it for it in items if it["id"] == bid)
    assert item["question"] == "研究生导师怎么选？"
    assert item["status"] == "PENDING"
    assert "导师" in item["suggested_keywords"]  # 预填建议


def test_admin_list_suggestions(api_client, db_session_factory):
    sid = _seed_suggestion(api_client)
    headers = _login(api_client, "admin-001")
    r = api_client.get("/api/admin/reviews?kind=suggestions", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["id"] == sid for it in items)
    assert all(it["status"] == "PENDING" for it in items)


def test_reviews_empty_list(api_client):
    headers = _login(api_client, "admin-001")
    r = api_client.get("/api/admin/reviews?kind=bad_cases", headers=headers)
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_admin_requires_role(api_client):
    headers = _login(api_client, "student-001")
    r = api_client.get("/api/admin/reviews?kind=bad_cases", headers=headers)
    assert r.status_code == 403
    r = api_client.post("/api/admin/reviews/bad_cases/1/adopt", headers=headers, json={})
    assert r.status_code == 403


def test_admin_requires_auth(api_client):
    r = api_client.get("/api/admin/reviews")
    assert r.status_code == 401


def test_reviews_invalid_kind_422(api_client):
    headers = _login(api_client, "admin-001")
    r = api_client.get("/api/admin/reviews?kind=whatever", headers=headers)
    assert r.status_code == 422


def test_adopt_bad_case_adds_knowledge_and_closes_loop(api_client, db_session_factory):
    bid = _seed_bad_case(api_client)
    r = _adopt(api_client, "bad_cases", bid)
    assert r.status_code == 200
    with db_session_factory() as session:
        bc = session.get(BadCase, bid)
        assert bc.status == "RESOLVED"
        entry = session.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.question == "研究生导师怎么选？")
        ).scalar_one()
        assert entry.domain == "教务"
        assert entry.type == "info"
        assert entry.keywords == "导师,研究生,选导师"
    # 闭环：补入后同问题检索命中
    hits = search_knowledge(db_session_factory, "研究生导师怎么选？")
    assert any(h["question"] == "研究生导师怎么选？" for h in hits)


def test_adopt_suggestion_sets_adopted(api_client, db_session_factory):
    sid = _seed_suggestion(api_client)
    r = _adopt(api_client, "suggestions", sid)
    assert r.status_code == 200
    with db_session_factory() as session:
        assert session.get(Suggestion, sid).status == "ADOPTED"
        assert session.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.question == "校车时刻表在哪查？")
        ).scalar_one()


def test_dismiss_bad_case_no_knowledge(api_client, db_session_factory):
    bid = _seed_bad_case(api_client)
    r = api_client.post(
        f"/api/admin/reviews/bad_cases/{bid}/dismiss",
        headers=_login(api_client, "admin-001"),
    )
    assert r.status_code == 200
    with db_session_factory() as session:
        assert session.get(BadCase, bid).status == "RESOLVED"
        # 驳回不补入：知识条目数保持种子 36 条不变
        assert len(session.execute(select(KnowledgeEntry)).scalars().all()) == 36


def test_reject_suggestion_sets_rejected(api_client, db_session_factory):
    sid = _seed_suggestion(api_client)
    r = api_client.post(
        f"/api/admin/reviews/suggestions/{sid}/dismiss",
        headers=_login(api_client, "admin-001"),
    )
    assert r.status_code == 200
    with db_session_factory() as session:
        assert session.get(Suggestion, sid).status == "REJECTED"
        assert len(session.execute(select(KnowledgeEntry)).scalars().all()) == 36


def test_adopt_processed_again_404(api_client, db_session_factory):
    bid = _seed_bad_case(api_client)
    assert _adopt(api_client, "bad_cases", bid).status_code == 200
    r = _adopt(api_client, "bad_cases", bid)
    assert r.status_code == 404
    r = api_client.post(
        f"/api/admin/reviews/bad_cases/{bid}/dismiss",
        headers=_login(api_client, "admin-001"),
    )
    assert r.status_code == 404


def test_unknown_id_404(api_client):
    headers = _login(api_client, "admin-001")
    r = api_client.get("/api/admin/reviews?kind=bad_cases", headers=headers)
    unknown = (max(it["id"] for it in r.json()["items"]) + 1) if r.json()["items"] else 1
    assert _adopt(api_client, "bad_cases", unknown).status_code == 404
    assert api_client.post(
        f"/api/admin/reviews/bad_cases/{unknown}/dismiss", headers=headers
    ).status_code == 404


def test_adopt_field_validation(api_client):
    bid = _seed_bad_case(api_client)
    assert _adopt(api_client, "bad_cases", bid, domain="无关领域").status_code == 422
    assert _adopt(api_client, "bad_cases", bid, type="unknown").status_code == 422
    assert _adopt(api_client, "bad_cases", bid, keywords="  ").status_code == 422
    assert _adopt(api_client, "bad_cases", bid, answer="").status_code == 422
