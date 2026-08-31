"""M15A-③ 登录失败锁定 + 三条护栏。

护栏（对抗性审查结论，逐条有测试）：
1. 只给真实存在的账号计数——不存在的用户名不进内存 dict（防内存被塞爆）
2. 锁定返回与"密码错误"完全一致的 401 文案——锁定事实只进审计，不泄露
3. 留解锁手段（管理员 user_mgmt 门控），否则可被反向 DoS 锁死管理员 15 分钟

补充口径：
- 计数按 **用户实体**（users.id），不按输入的用户名——学生可用 id 或学号登录，
  按用户名计数会导致"学号试满 5 次再换 id 试"绕过锁定
- 禁用账号（403）不算密码失败，不计数
- 失败审计只记锁定/解锁事件，不每次失败都写（否则账号锁反成写库放大器）
"""

import time

import pytest
from sqlalchemy import select

from campus_desk import rate_limit
from campus_desk.db.models import AuditLog, User

_PASSWORD = "123456"


@pytest.fixture(autouse=True)
def _clean_lock_state():
    """内存计数是模块级全局，测试间必须隔离。"""
    rate_limit.reset_all()
    yield
    rate_limit.reset_all()


def _login(client, username, password=_PASSWORD):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def _token(client, username):
    r = _login(client, username)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _lock(client, username="student-001"):
    for _ in range(rate_limit.MAX_FAILS):
        _login(client, username, "wrong-pw")


def _audit_actions(db_session_factory):
    with db_session_factory() as session:
        return [a.action for a in session.execute(select(AuditLog)).scalars().all()]


# ---------- 基础锁定 ----------


def test_locks_after_max_fails(api_client):
    _lock(api_client)
    # 第 6 次用正确密码，仍被拒
    assert _login(api_client, "student-001").status_code == 401


def test_below_threshold_still_works(api_client):
    for _ in range(rate_limit.MAX_FAILS - 1):
        assert _login(api_client, "student-001", "wrong-pw").status_code == 401
    assert _login(api_client, "student-001").status_code == 200


def test_lock_expires_after_window(api_client, monkeypatch):
    monkeypatch.setattr(rate_limit, "LOCK_SECONDS", 1)
    _lock(api_client)
    assert _login(api_client, "student-001").status_code == 401
    time.sleep(1.1)
    assert _login(api_client, "student-001").status_code == 200


# ---------- 护栏 2：不泄露锁定状态 ----------


def test_locked_message_identical_to_wrong_password(api_client):
    wrong_detail = _login(api_client, "student-001", "wrong-pw").json()["detail"]
    for _ in range(rate_limit.MAX_FAILS - 1):
        _login(api_client, "student-001", "wrong-pw")
    locked_detail = _login(api_client, "student-001").json()["detail"]

    assert locked_detail == wrong_detail
    assert "锁定" not in locked_detail


# ---------- 护栏 1：不存在的账号不计数 ----------


def test_unknown_user_not_counted(api_client):
    for _ in range(rate_limit.MAX_FAILS + 2):
        assert _login(api_client, "no-such-user", "x").status_code == 401
    assert rate_limit._FAILS == {}
    assert rate_limit._LOCKED_UNTIL == {}
    assert _login(api_client, "student-001").status_code == 200


# ---------- 计数按用户实体：学号 / id 不可绕过 ----------


def test_alt_login_identifier_shares_counter(api_client):
    """student-001 学号 2024001：混用两种登录名试错不能绕过锁定。"""
    for _ in range(2):
        _login(api_client, "2024001", "wrong-pw")
    for _ in range(3):
        _login(api_client, "student-001", "wrong-pw")
    assert _login(api_client, "student-001").status_code == 401


# ---------- 成功登录清计数 ----------


def test_success_resets_counter(api_client):
    for _ in range(rate_limit.MAX_FAILS - 1):
        _login(api_client, "student-001", "wrong-pw")
    assert _login(api_client, "student-001").status_code == 200  # 清 0
    for _ in range(rate_limit.MAX_FAILS - 1):
        _login(api_client, "student-001", "wrong-pw")
    assert _login(api_client, "student-001").status_code == 200  # 未锁


# ---------- 禁用账号不计数 ----------


def test_disabled_user_not_counted(api_client, db_session_factory):
    with db_session_factory() as session, session.begin():
        session.get(User, "student-003").enabled = False

    r = _login(api_client, "student-003")
    assert r.status_code == 403
    assert "student-003" not in rate_limit._FAILS


# ---------- 护栏 3：管理员解锁 ----------


def test_admin_can_unlock(api_client):
    _lock(api_client)
    assert _login(api_client, "student-001").status_code == 401

    r = api_client.post(
        "/api/admin/users/student-001/unlock", headers=_token(api_client, "admin-001")
    )
    assert r.status_code == 200
    assert _login(api_client, "student-001").status_code == 200


def test_unlock_requires_permission(api_client):
    r = api_client.post(
        "/api/admin/users/student-001/unlock", headers=_token(api_client, "student-002")
    )
    assert r.status_code == 403


def test_unlock_unknown_user_404(api_client):
    r = api_client.post(
        "/api/admin/users/no-such-user/unlock", headers=_token(api_client, "admin-001")
    )
    assert r.status_code == 404


# ---------- 审计粒度（F4：失败不刷表，锁定/解锁才记）----------


def test_single_failure_writes_no_audit(api_client, db_session_factory):
    _login(api_client, "student-001", "wrong-pw")
    assert _audit_actions(db_session_factory) == []


def test_lock_and_unlock_are_audited(api_client, db_session_factory):
    _lock(api_client)
    actions = _audit_actions(db_session_factory)
    assert actions.count("login_locked") == 1

    api_client.post(
        "/api/admin/users/student-001/unlock", headers=_token(api_client, "admin-001")
    )
    assert "login_unlocked" in _audit_actions(db_session_factory)
