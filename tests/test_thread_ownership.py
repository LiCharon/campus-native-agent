"""M15A-⑦ 会话归属校验（共享 helper，chat 与 feedback 同一口径）。

背景：/api/chat 在 M5 就做了归属校验，/api/feedback/bad-case 只验了"登录了"、
没验"这条会话是不是你的"——于是可以拿别人会话 ID 往反馈表塞脏数据。

口径：一律 404，不区分"thread 不存在"与"存在但属于别人"——区分反而泄露
"该 thread 存在"这一事实。与 /api/chat 现有行为保持一致。
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from campus_desk.api.deps import get_owned_conversation
from campus_desk.db.models import BadCase

_PASSWORD = "123456"


def _auth(client, username="student-001"):
    r = client.post(
        "/api/auth/login", json={"username": username, "password": _PASSWORD}
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _new_thread(client, headers):
    r = client.post("/api/sessions", headers=headers)
    assert r.status_code == 200
    return r.json()["thread_id"]


def _post_bad_case(client, headers, thread_id):
    return client.post(
        "/api/feedback/bad-case",
        headers=headers,
        json={"thread_id": thread_id, "question": "这个问题没解决"},
    )


def _bad_case_rows(db_session_factory):
    with db_session_factory() as session:
        return session.execute(select(BadCase)).scalars().all()


# ---------- helper 本身 ----------


def test_helper_raises_404_when_not_owned(db_session_factory):
    with db_session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            get_owned_conversation(session, "no-such-thread", "student-001")
        assert exc.value.status_code == 404


# ---------- /api/feedback 归属校验（本次修复面）----------


def test_feedback_rejects_other_users_thread(api_client, db_session_factory):
    """核心：拿别人的 thread_id 提交反馈 → 404，且不落库。"""
    owner = _auth(api_client, "student-001")
    tid = _new_thread(api_client, owner)

    other = _auth(api_client, "student-002")
    r = _post_bad_case(api_client, other, tid)

    assert r.status_code == 404
    assert _bad_case_rows(db_session_factory) == []


def test_feedback_rejects_nonexistent_thread(api_client, db_session_factory):
    """伪造不存在的 thread → 同样 404（与越权同文案，不泄露存在性）。"""
    headers = _auth(api_client)
    r = _post_bad_case(api_client, headers, str(uuid.uuid4()))

    assert r.status_code == 404
    assert _bad_case_rows(db_session_factory) == []


def test_feedback_accepts_own_thread(api_client, db_session_factory):
    """自己的会话照常可用——校验不能误伤正常路径。"""
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    r = _post_bad_case(api_client, headers, tid)

    assert r.status_code == 200
    rows = _bad_case_rows(db_session_factory)
    assert len(rows) == 1
    assert rows[0].user_id == "student-001"
    assert rows[0].thread_id == tid


def test_feedback_requires_auth(api_client):
    r = _post_bad_case(api_client, {}, str(uuid.uuid4()))
    assert r.status_code == 401


# ---------- /api/chat 回归（换成共享 helper 后行为不得变）----------


def test_chat_rejects_other_users_thread(api_client):
    owner = _auth(api_client, "student-001")
    tid = _new_thread(api_client, owner)

    other = _auth(api_client, "student-002")
    r = api_client.post(
        "/api/chat", headers=other, json={"thread_id": tid, "msg": "你好"}
    )
    assert r.status_code == 404


def test_chat_accepts_own_thread(api_client):
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    r = api_client.post(
        "/api/chat", headers=headers, json={"thread_id": tid, "msg": "图书馆几点关门"}
    )
    assert r.status_code == 200
