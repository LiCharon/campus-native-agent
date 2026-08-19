"""M4 管理特权/权限位 API 测试：登录权限并集、授权、users CRUD、admin 保护、
审计日志、看板聚合、客服工作台、chat 来源 chip。

覆盖（M4 设计 v3 §2/§5 + 对抗性审查 #3/#4/#6）：
- 登录响应 permissions = 角色默认 ∪ 附加位
- 授予 kb_review 的 cs_staff 可访问审查接口；未授权 403
- users 创建/禁用/恢复/重置密码；admin 不可禁用降权；student 不可带附加位
- audit_logs 埋点（登录）与查询
- stats 聚合口径（users count / knowledge count / RESOLVED 总数）
- cs queue（cs_staff+admin 可看）resolve（仅 cs_staff）幂等 404
- chat 响应 sources（知识命中 → kb 来源 chip）
"""

from campus_desk.db.models import BadCase

_ALL_PERMS = ["chat", "cs_workbench", "kb_review", "view_stats", "user_mgmt", "view_logs"]


def _login(client, username, password="123456"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"login {username}: {r.status_code} {r.text}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _login_data(client, username, password="123456"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _seed_bad_case(client):
    stu = _login(client, "student-001")
    r = client.post(
        "/api/feedback/bad-case",
        headers=stu,
        json={"thread_id": "m4-1", "question": "食堂在哪？", "reply": "超出范围", "note": ""},
    )
    assert r.status_code == 200
    return r.json()["id"]


# ---------- 登录权限并集 ----------


def test_login_permissions_by_role(api_client):
    r = _login_data(api_client, "student-001")
    assert r.json()["user"]["permissions"] == ["chat"]
    r = _login_data(api_client, "cs-001")
    assert r.json()["user"]["permissions"] == ["chat", "cs_workbench"]
    r = _login_data(api_client, "admin-001")
    assert r.json()["user"]["permissions"] == _ALL_PERMS


# ---------- 权限位授予（对抗性审查 #4 场景） ----------


def test_grant_kb_review_to_cs_staff(api_client):
    admin = _login(api_client, "admin-001")
    # 未授权时 cs-001 访问审查接口 403
    cs = _login(api_client, "cs-001")
    assert api_client.get("/api/admin/reviews?kind=bad_cases", headers=cs).status_code == 403
    # 授 kb_review
    r = api_client.put(
        "/api/admin/users/cs-001",
        headers=admin,
        json={"role": "cs_staff", "permissions": ["kb_review"], "enabled": True},
    )
    assert r.status_code == 200
    # 重登后生效（JWT 无状态，改权限需重登）
    cs2 = _login(api_client, "cs-001")
    r = api_client.get("/api/admin/reviews?kind=bad_cases", headers=cs2)
    assert r.status_code == 200


def test_student_denied_all_admin(api_client):
    stu = _login(api_client, "student-001")
    for path in [
        "/api/admin/reviews?kind=bad_cases",
        "/api/admin/knowledge",
        "/api/admin/stats",
        "/api/admin/users",
        "/api/admin/logs",
        "/api/cs/queue",
    ]:
        assert api_client.get(path, headers=stu).status_code == 403, path


# ---------- users CRUD + 保护 ----------


def test_create_user_then_login(api_client):
    admin = _login(api_client, "admin-001")
    r = api_client.post(
        "/api/admin/users",
        headers=admin,
        json={
            "id": "student-099",
            "name": "测试生",
            "role": "student",
            "student_no": "2099",
            "password": "secret99",
            "permissions": [],
        },
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    # 初始密码可登录
    assert _login_data(api_client, "student-099", "secret99").status_code == 200
    # 重复创建 409
    assert (
        api_client.post(
            "/api/admin/users",
            headers=admin,
            json={
                "id": "student-099",
                "name": "x",
                "role": "student",
                "password": "secret99",
                "permissions": [],
            },
        ).status_code
        == 409
    )


def test_disable_user_blocks_login(api_client):
    admin = _login(api_client, "admin-001")
    api_client.post(
        "/api/admin/users",
        headers=admin,
        json={
            "id": "student-098",
            "name": "禁测",
            "role": "student",
            "password": "secret98",
            "permissions": [],
        },
    )
    r = api_client.put(
        "/api/admin/users/student-098",
        headers=admin,
        json={"role": "student", "permissions": [], "enabled": False},
    )
    assert r.status_code == 200
    assert _login_data(api_client, "student-098", "secret98").status_code == 403
    # 恢复
    api_client.put(
        "/api/admin/users/student-098",
        headers=admin,
        json={"role": "student", "permissions": [], "enabled": True},
    )
    assert _login_data(api_client, "student-098", "secret98").status_code == 200


def test_reset_password(api_client):
    admin = _login(api_client, "admin-001")
    api_client.post(
        "/api/admin/users",
        headers=admin,
        json={
            "id": "student-095",
            "name": "密测",
            "role": "student",
            "password": "oldpass99",
            "permissions": [],
        },
    )
    r = api_client.post(
        "/api/admin/users/student-095/reset-password",
        headers=admin,
        json={"password": "newpass99"},
    )
    assert r.status_code == 200
    assert _login_data(api_client, "student-095", "oldpass99").status_code == 401
    assert _login_data(api_client, "student-095", "newpass99").status_code == 200


def test_admin_protected_from_disable_and_demote(api_client):
    admin = _login(api_client, "admin-001")
    r = api_client.put(
        "/api/admin/users/admin-001",
        headers=admin,
        json={"role": "admin", "permissions": [], "enabled": False},
    )
    assert r.status_code == 403
    r = api_client.put(
        "/api/admin/users/admin-001",
        headers=admin,
        json={"role": "student", "permissions": [], "enabled": True},
    )
    assert r.status_code == 403


def test_student_cannot_carry_extra_permissions(api_client):
    admin = _login(api_client, "admin-001")
    r = api_client.post(
        "/api/admin/users",
        headers=admin,
        json={
            "id": "student-097",
            "name": "x",
            "role": "student",
            "password": "secret97",
            "permissions": ["view_stats"],
        },
    )
    assert r.status_code == 422
    r = api_client.post(
        "/api/admin/users",
        headers=admin,
        json={
            "id": "student-096",
            "name": "x",
            "role": "student",
            "password": "secret96",
            "permissions": ["not_a_perm"],
        },
    )
    assert r.status_code == 422


# ---------- 审计日志 ----------


def test_audit_log_written_on_login_and_queryable(api_client):
    _login(api_client, "student-001")
    _login(api_client, "admin-001")
    admin = _login(api_client, "admin-001")
    r = api_client.get("/api/admin/logs", headers=admin)
    assert r.status_code == 200
    actions = [it["action"] for it in r.json()["items"]]
    assert actions.count("login") >= 2
    # 筛选
    r = api_client.get("/api/admin/logs?action=login&user_id=admin-001", headers=admin)
    assert all(it["user_id"] == "admin-001" and it["action"] == "login" for it in r.json()["items"])


def test_audit_written_on_adopt_and_resolve(api_client, db_session_factory):
    # 两个独立 bad_case：一个走客服 resolve，一个走 admin adopt
    rid = _seed_bad_case(api_client)
    aid = _seed_bad_case(api_client)
    cs = _login(api_client, "cs-001")
    api_client.post(f"/api/cs/{rid}/resolve", headers=cs)
    admin = _login(api_client, "admin-001")
    r = api_client.get("/api/admin/logs?action=cs_resolve", headers=admin)
    assert any(it["object_id"] == str(rid) for it in r.json()["items"])
    # adopt 审计
    api_client.post(
        f"/api/admin/reviews/bad_cases/{aid}/adopt",
        headers=admin,
        json={"domain": "住宿后勤", "type": "info", "keywords": "食堂", "answer": "xxx"},
    )
    r = api_client.get("/api/admin/logs?action=adopt", headers=admin)
    assert any(it["object_id"] == str(aid) for it in r.json()["items"])


# ---------- 看板聚合（对抗性审查 #6 口径） ----------


def test_stats_aggregation(api_client, db_session_factory):
    admin = _login(api_client, "admin-001")
    r = api_client.get("/api/admin/stats", headers=admin)
    assert r.status_code == 200
    data = r.json()
    assert data["user_count"] == 5  # 种子 5 账号
    assert data["knowledge_count"] == 36  # 种子 36 条
    # type 分布按种子实际计数（36 = info 13 + process 8 + index 15）
    assert sum(data["type_dist"].values()) == 36
    assert len(data["feedback_by_day"]) == 14  # 近 14 天补零


# ---------- 客服工作台 ----------


def test_cs_queue_and_resolve(api_client, db_session_factory):
    bid = _seed_bad_case(api_client)
    cs = _login(api_client, "cs-001")
    admin = _login(api_client, "admin-001")
    # cs_staff 可看
    r = api_client.get("/api/cs/queue", headers=cs)
    assert r.status_code == 200
    assert any(it["id"] == bid for it in r.json()["items"])
    # admin 可看（只读）
    assert api_client.get("/api/cs/queue", headers=admin).status_code == 200
    # 标记已处理
    r = api_client.post(f"/api/cs/{bid}/resolve", headers=cs)
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    with db_session_factory() as session:
        assert session.get(BadCase, bid).status == "RESOLVED"
    # 幂等：已处理再 resolve 404
    assert api_client.post(f"/api/cs/{bid}/resolve", headers=cs).status_code == 404
    # admin 不能 resolve（接待与审查职责分离）
    bid2 = _seed_bad_case(api_client)
    assert api_client.post(f"/api/cs/{bid2}/resolve", headers=admin).status_code == 403


# ---------- chat 来源 chip ----------


def test_chat_sources_kb_hit(api_client):
    """知识命中 → sources 含 kb 来源 chip（#K{id} {type}型 · {domain}）。"""
    stu = _login(api_client, "student-001")
    r = api_client.post(
        "/api/chat",
        headers=stu,
        json={"thread_id": "m4-src-1", "msg": "什么时候放寒假？"},
    )
    assert r.status_code == 200
    data = r.json()
    sources = data.get("sources", [])
    assert sources, "知识命中应带 sources"
    kb = [s for s in sources if s["type"] == "kb"]
    assert kb and kb[0]["ref_id"].startswith("#K")
    assert "教务" in kb[0]["detail"]
