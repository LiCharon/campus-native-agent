"""会话 API 测试（M5-ZJUT）：/api/sessions 增删改查 + /api/chat 归属校验与落库。

覆盖：未登录 401 / 创建 / 列表用户隔离 / 重命名 / 跨用户 404 / 删除级联 /
消息顺序与 sources 解析 / chat 归属校验 / chat 落库 / 自动标题 / 手动改名不覆盖 / handoff。
"""

from sqlalchemy import select

from campus_desk.db.models import Conversation, Message


def _headers(api_client, username="student-001"):
    login = api_client.post(
        "/api/auth/login", json={"username": username, "password": "123456"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _create(api_client, headers):
    resp = api_client.post("/api/sessions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_knowledge(db_session_factory):
    from campus_desk.db.models import KnowledgeEntry

    with db_session_factory() as s, s.begin():
        s.add(
            KnowledgeEntry(
                domain="教务",
                keywords="校历,寒假",
                question="什么时候放寒假？",
                type="info",
                answer="寒假以学校通知为准。",
            )
        )


def test_sessions_requires_auth(api_client):
    assert api_client.get("/api/sessions").status_code == 401
    assert api_client.post("/api/sessions").status_code == 401
    assert api_client.patch("/api/sessions/x", json={"title": "t"}).status_code == 401
    assert api_client.delete("/api/sessions/x").status_code == 401
    assert api_client.get("/api/sessions/x/messages").status_code == 401


def test_create_session(api_client):
    headers = _headers(api_client)
    conv = _create(api_client, headers)
    assert conv["id"]
    assert conv["thread_id"]
    assert conv["title"] == "新对话"
    assert conv["title_source"] == "auto"
    assert conv["handoff"] == "none"
    assert conv["created_at"] and conv["updated_at"]


def test_list_sessions_user_isolation(api_client):
    _create(api_client, _headers(api_client, "student-001"))
    # 另一用户看不到 student-001 的会话
    resp = api_client.get("/api/sessions", headers=_headers(api_client, "cs-001"))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_rename_session(api_client):
    headers = _headers(api_client)
    conv = _create(api_client, headers)
    resp = api_client.patch(
        f"/api/sessions/{conv['id']}", json={"title": "改名啦"}, headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "改名啦"
    assert data["title_source"] == "manual"


def test_update_other_user_session_404(api_client):
    conv = _create(api_client, _headers(api_client, "student-001"))
    other = _headers(api_client, "cs-001")
    assert (
        api_client.patch(
            f"/api/sessions/{conv['id']}", json={"title": "x"}, headers=other
        ).status_code
        == 404
    )
    assert (
        api_client.delete(f"/api/sessions/{conv['id']}", headers=other).status_code
        == 404
    )
    assert (
        api_client.get(
            f"/api/sessions/{conv['id']}/messages", headers=other
        ).status_code
        == 404
    )


def test_delete_session_cascades_messages(api_client, db_session_factory):
    _seed_knowledge(db_session_factory)
    headers = _headers(api_client)
    conv = _create(api_client, headers)
    api_client.post(
        "/api/chat",
        json={"thread_id": conv["thread_id"], "msg": "什么时候放寒假？"},
        headers=headers,
    )
    # 删除前：会话 + 2 条消息
    with db_session_factory() as s:
        assert s.execute(select(Conversation)).scalars().all()
        assert len(s.execute(select(Message)).scalars().all()) == 2
    resp = api_client.delete(f"/api/sessions/{conv['id']}", headers=headers)
    assert resp.status_code == 200
    # 级联：messages 随删
    with db_session_factory() as s:
        assert s.execute(select(Conversation)).scalars().all() == []
        assert s.execute(select(Message)).scalars().all() == []
    # 再访问 404
    assert (
        api_client.get(f"/api/sessions/{conv['id']}/messages", headers=headers).status_code
        == 404
    )


def test_list_messages_order_and_sources(api_client, db_session_factory):
    _seed_knowledge(db_session_factory)
    headers = _headers(api_client)
    conv = _create(api_client, headers)
    resp = api_client.post(
        "/api/chat",
        json={"thread_id": conv["thread_id"], "msg": "什么时候放寒假？"},
        headers=headers,
    )
    assert resp.status_code == 200
    resp = api_client.get(f"/api/sessions/{conv['id']}/messages", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [m["role"] for m in items] == ["user", "assistant"]
    # assistant 消息携带 outcome 与 sources（knowledge 命中 → kb 来源）
    assistant = items[1]
    assert assistant["outcome"] == "answer"
    assert assistant["sources"], "知识命中应产生 kb 来源 chip"
    assert assistant["sources"][0]["type"] == "kb"
    assert "寒假" in items[0]["content"]


def test_chat_requires_owned_session(api_client):
    """严格归属校验：thread_id 未建于会话库 → 404（不做懒创建）。"""
    headers = _headers(api_client)
    resp = api_client.post(
        "/api/chat", json={"thread_id": "no-such-thread", "msg": "hi"}, headers=headers
    )
    assert resp.status_code == 404


def test_chat_persists_messages(api_client, db_session_factory):
    _seed_knowledge(db_session_factory)
    headers = _headers(api_client)
    conv = _create(api_client, headers)
    resp = api_client.post(
        "/api/chat",
        json={"thread_id": conv["thread_id"], "msg": "什么时候放寒假？"},
        headers=headers,
    )
    assert resp.status_code == 200
    with db_session_factory() as s:
        msgs = s.execute(
            select(Message).order_by(Message.id)
        ).scalars().all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "什么时候放寒假？"
    assert msgs[1].outcome == "answer"
    assert msgs[1].sources  # JSON 已存


def test_auto_title_from_first_message(api_client, db_session_factory):
    _seed_knowledge(db_session_factory)
    headers = _headers(api_client)
    conv = _create(api_client, headers)
    msg = "请问校园卡丢了要怎么挂失和补办啊"
    api_client.post(
        "/api/chat", json={"thread_id": conv["thread_id"], "msg": msg}, headers=headers
    )
    resp = api_client.get("/api/sessions", headers=headers)
    item = resp.json()["items"][0]
    # 去空白前 12 字 + "…"（16 字 > 12）
    assert item["title"] == msg.replace(" ", "")[:12] + "…"
    assert item["title_source"] == "auto"


def test_manual_title_not_overwritten(api_client, db_session_factory):
    _seed_knowledge(db_session_factory)
    headers = _headers(api_client)
    conv = _create(api_client, headers)
    api_client.patch(
        f"/api/sessions/{conv['id']}", json={"title": "我的重要会话"}, headers=headers
    )
    api_client.post(
        "/api/chat",
        json={"thread_id": conv["thread_id"], "msg": "什么时候放寒假？"},
        headers=headers,
    )
    resp = api_client.get("/api/sessions", headers=headers)
    item = resp.json()["items"][0]
    assert item["title"] == "我的重要会话"
    assert item["title_source"] == "manual"


def test_handoff_patch(api_client):
    headers = _headers(api_client)
    conv = _create(api_client, headers)
    resp = api_client.patch(
        f"/api/sessions/{conv['id']}", json={"handoff": "human"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["handoff"] == "human"
    resp = api_client.get("/api/sessions", headers=headers)
    assert resp.json()["items"][0]["handoff"] == "human"


def test_session_update_requires_field(api_client):
    headers = _headers(api_client)
    conv = _create(api_client, headers)
    resp = api_client.patch(f"/api/sessions/{conv['id']}", json={}, headers=headers)
    assert resp.status_code == 422
