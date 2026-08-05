"""M6 API 登录鉴权测试：账号/学号都能登、错密码 401、无 token 401、RBAC 403。"""


def _login(client, username="student-001", password="123456"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"]
    return body


class TestLogin:
    def test_login_by_user_id(self, api_client):
        body = _login(api_client, "student-001")
        assert body["user"]["role"] == "student"
        assert body["user"]["name"] == "李华"
        assert body["expires_in"] > 0

    def test_login_by_student_no(self, api_client):
        # 学号也能登（users.student_no 匹配）
        body = _login(api_client, "2024001")
        assert body["user"]["id"] == "student-001"

    def test_wrong_password_401(self, api_client):
        r = api_client.post(
            "/api/auth/login", json={"username": "student-001", "password": "wrong"}
        )
        assert r.status_code == 401

    def test_unknown_user_401(self, api_client):
        r = api_client.post(
            "/api/auth/login", json={"username": "nobody", "password": "123456"}
        )
        assert r.status_code == 401


class TestAuthGuard:
    def test_chat_without_token_401(self, api_client):
        r = api_client.post("/api/chat", json={"thread_id": "t", "msg": "hi"})
        assert r.status_code == 401

    def test_tickets_without_token_401(self, api_client):
        assert api_client.get("/api/tickets").status_code == 401

    def test_bad_token_401(self, api_client):
        r = api_client.get("/api/tickets", headers={"Authorization": "Bearer not-a-token"})
        assert r.status_code == 401

    def test_dashboard_student_403(self, api_client):
        body = _login(api_client, "student-001")
        r = api_client.get(
            "/api/dashboard", headers={"Authorization": f"Bearer {body['token']}"}
        )
        assert r.status_code == 403

    def test_dashboard_admin_200(self, api_client):
        body = _login(api_client, "admin-001")
        r = api_client.get(
            "/api/dashboard", headers={"Authorization": f"Bearer {body['token']}"}
        )
        assert r.status_code == 200
        assert "total" in r.json()

    def test_assign_student_403(self, api_client):
        body = _login(api_client, "student-001")
        r = api_client.post(
            "/api/admin/tickets/1/assign",
            json={"repairman_id": "rm-001", "dept": "后勤"},
            headers={"Authorization": f"Bearer {body['token']}"},
        )
        assert r.status_code == 403
