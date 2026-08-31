"""M15A-⑥ 请求体长度上限（422 拒绝，不静默截断）。

动机：/api/chat 的 msg 直接进 LLM 与 messages 表，无上限意味着一次请求就能
刷爆 DeepSeek 账单（M13 成本计量已量化单次开销）或灌进超长行。
截断是不可接受的——用户会以为自己发全了。

边界内值用 schema 直验（走 HTTP 会触发真检索/embedding，慢）；
超限走 HTTP 断言 422（FastAPI 在校验阶段即拒，不进路由）。
"""

import pytest
from pydantic import ValidationError

from campus_desk.api.schemas import (
    MAX_MSG_LEN,
    MAX_NOTE_LEN,
    MAX_QUESTION_LEN,
    MAX_REPLY_LEN,
    MAX_THREAD_ID_LEN,
    ChatRequest,
    FeedbackBadCaseRequest,
    FeedbackSuggestionRequest,
)


def _auth(client, username="student-001", password="123456"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _new_thread(client, headers):
    """真实存在的 thread_id（服务端生成）。

    ⚠️ 不可编造：/api/chat 有归属校验（M5 硬约束），不存在的 thread_id 返回 404，
    会让"超长被拒"的断言变成假阳性——必须排除这条歧义路径。
    """
    r = client.post("/api/sessions", headers=headers)
    assert r.status_code == 200
    return r.json()["thread_id"]


def _assert_too_long(r):
    """断言 422 的原因确实是长度，而不是别的校验失败。"""
    assert r.status_code == 422
    assert "at most" in str(r.json())


# ---------- /api/chat ----------


def test_chat_msg_at_limit_accepted():
    ChatRequest(thread_id="t-1", msg="x" * MAX_MSG_LEN)


def test_chat_msg_over_limit_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(thread_id="t-1", msg="x" * (MAX_MSG_LEN + 1))


def test_chat_msg_over_limit_returns_422(api_client):
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    r = api_client.post(
        "/api/chat",
        headers=headers,
        json={"thread_id": tid, "msg": "x" * (MAX_MSG_LEN + 1)},
    )
    _assert_too_long(r)


def test_chat_thread_id_over_limit_returns_422(api_client):
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    real_but_too_long = tid + "x" * (MAX_THREAD_ID_LEN + 1 - len(tid))
    r = api_client.post(
        "/api/chat",
        headers=headers,
        json={"thread_id": real_but_too_long, "msg": "你好"},
    )
    _assert_too_long(r)


def test_chat_normal_input_still_works(api_client):
    """限长不能误伤正常短消息。"""
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    r = api_client.post(
        "/api/chat", headers=headers, json={"thread_id": tid, "msg": "图书馆几点关门"}
    )
    assert r.status_code == 200


# ---------- /api/feedback ----------


def test_bad_case_thread_id_over_limit_returns_422(api_client):
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    real_but_too_long = tid + "x" * (MAX_THREAD_ID_LEN + 1 - len(tid))
    r = api_client.post(
        "/api/feedback/bad-case",
        headers=headers,
        json={"thread_id": real_but_too_long, "question": "问题"},
    )
    _assert_too_long(r)


def test_bad_case_question_over_limit_returns_422(api_client):
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    r = api_client.post(
        "/api/feedback/bad-case",
        headers=headers,
        json={"thread_id": tid, "question": "x" * (MAX_QUESTION_LEN + 1)},
    )
    _assert_too_long(r)


def test_bad_case_reply_over_limit_returns_422(api_client):
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    r = api_client.post(
        "/api/feedback/bad-case",
        headers=headers,
        json={"thread_id": tid, "question": "问题", "reply": "x" * (MAX_REPLY_LEN + 1)},
    )
    _assert_too_long(r)


def test_bad_case_note_over_limit_returns_422(api_client):
    headers = _auth(api_client)
    tid = _new_thread(api_client, headers)
    r = api_client.post(
        "/api/feedback/bad-case",
        headers=headers,
        json={"thread_id": tid, "question": "问题", "note": "x" * (MAX_NOTE_LEN + 1)},
    )
    _assert_too_long(r)


def test_suggestion_note_over_limit_returns_422(api_client):
    headers = _auth(api_client)
    r = api_client.post(
        "/api/feedback/suggestion",
        headers=headers,
        json={"question": "问题", "note": "x" * (MAX_NOTE_LEN + 1)},
    )
    assert r.status_code == 422


def test_feedback_at_limits_accepted():
    FeedbackBadCaseRequest(
        thread_id="t" * MAX_THREAD_ID_LEN,
        question="x" * MAX_QUESTION_LEN,
        reply="x" * MAX_REPLY_LEN,
        note="x" * MAX_NOTE_LEN,
    )
    FeedbackSuggestionRequest(question="x" * MAX_QUESTION_LEN, note="x" * MAX_NOTE_LEN)
