"""对话 API 测试（M1-T8）：登录 → /api/chat 走 knowledge 真检索；未登录 401。

FakeClassifier 恒返 knowledge 意图 → 门控放行 → KnowledgeGraph 真检索
（db_session_factory 注入的知识条目），断言 route/outcome/reply。
"""


def test_chat_knowledge_flow(api_client, db_session_factory):
    from campus_desk.db.models import KnowledgeEntry

    with db_session_factory() as s, s.begin():
        s.add(KnowledgeEntry(domain="教务", keywords="校历,寒假", question="什么时候放寒假？", type="info", answer="寒假以学校通知为准。"))
    login = api_client.post("/api/auth/login", json={"username": "student-001", "password": "123456"})
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    resp = api_client.post("/api/chat", json={"thread_id": "t-1", "msg": "什么时候放寒假？"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "knowledge"
    assert data["outcome"] == "answer"
    assert "寒假" in data["reply"]


def test_chat_requires_auth(api_client):
    resp = api_client.post("/api/chat", json={"thread_id": "t-1", "msg": "hi"})
    assert resp.status_code == 401


def test_chat_response_has_tool_calls_contract(api_client):
    """M2：ChatResponse.tool_calls 契约字段存在（knowledge 路径为空列表）。

    工具调用行为已在 tests/test_query_graph.py 图级覆盖；此处锁 API 契约透传
    字段（orchestrator 对 query 路由返回 tool_calls，对 knowledge 路由返回空）。
    """
    login = api_client.post("/api/auth/login", json={"username": "student-001", "password": "123456"})
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    resp = api_client.post("/api/chat", json={"thread_id": "t-1", "msg": "什么时候放寒假？"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "tool_calls" in data
    assert data["tool_calls"] == []
