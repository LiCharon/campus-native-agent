"""进化闭环反馈 API 测试（M3）：对话页"没解决"手动 bad case + 提建议通道。

覆盖（设计 §5.5 双通道）：
- bad-case 手动反馈：写 bad_cases（PENDING），thread_id/question/reply/note 落库
- suggestion 提议：写 suggestions（PENDING）
- 鉴权：未登录 401
- 校验：question 空白 422
"""

from sqlalchemy import select

from campus_desk.db.models import BadCase, Suggestion


def _auth(client, username="student-001", password="123456"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _new_thread(client, headers):
    """M15A-⑦：feedback 现在校验 thread_id 归属，编造的 thread_id 会 404。"""
    r = client.post("/api/sessions", headers=headers)
    assert r.status_code == 200
    return r.json()["thread_id"]


def test_bad_case_feedback_writes_row(api_client, db_session_factory):
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    r = api_client.post(
        "/api/feedback/bad-case",
        headers=headers,
        json={
            "thread_id": tid,
            "question": "研究生导师怎么选？",
            "reply": "抱歉，这个问题超出我的知识范围",
            "note": "没解决",
        },
    )
    assert r.status_code == 200
    with db_session_factory() as session:
        row = session.execute(select(BadCase)).scalar_one()
        assert row.user_id == "student-001"
        assert row.thread_id == tid
        assert row.question == "研究生导师怎么选？"
        assert row.reply == "抱歉，这个问题超出我的知识范围"
        assert row.note == "没解决"
        assert row.status == "PENDING"


def test_bad_case_note_reply_optional(api_client, db_session_factory):
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    r = api_client.post(
        "/api/feedback/bad-case", headers=headers, json={"thread_id": tid, "question": "q"}
    )
    assert r.status_code == 200
    with db_session_factory() as session:
        row = session.execute(select(BadCase)).scalar_one()
        assert row.reply == ""
        assert row.note == ""


def test_suggestion_writes_row(api_client, db_session_factory):
    headers = _auth(api_client)
    r = api_client.post(
        "/api/feedback/suggestion",
        headers=headers,
        json={"question": "研究生导师怎么选？", "note": "希望能有答案"},
    )
    assert r.status_code == 200
    with db_session_factory() as session:
        row = session.execute(select(Suggestion)).scalar_one()
        assert row.user_id == "student-001"
        assert row.question == "研究生导师怎么选？"
        assert row.note == "希望能有答案"
        assert row.status == "PENDING"


def test_feedback_requires_auth(api_client):
    r = api_client.post("/api/feedback/bad-case", json={"thread_id": "t", "question": "q"})
    assert r.status_code == 401
    r = api_client.post("/api/feedback/suggestion", json={"question": "q"})
    assert r.status_code == 401


def test_feedback_blank_question_422(api_client):
    headers = _auth(api_client)
    r = api_client.post(
        "/api/feedback/bad-case",
        headers=headers,
        json={"thread_id": "t", "question": "   "},
    )
    assert r.status_code == 422
    r = api_client.post("/api/feedback/suggestion", headers=headers, json={"question": ""})
    assert r.status_code == 422
