"""登录鉴权 API 测试（M1-T8）：账号/学号登录 + 密码错/未知用户 401。

T1 spec 审查登记必做项（M6 前端登录依赖 JWT 契约）：
- 账号（users.id）登录 → 200 + token + user.role
- 学号（student_no）登录 → 200 + user.id 归一为账号
- 密码错误 → 401
- 未知用户 → 401
"""


def test_login_with_account(api_client):
    r = api_client.post("/api/auth/login", json={"username": "student-001", "password": "123456"})
    assert r.status_code == 200
    data = r.json()
    assert data["token"]
    assert data["user"]["role"] == "student"


def test_login_with_student_no(api_client):
    r = api_client.post("/api/auth/login", json={"username": "2024001", "password": "123456"})
    assert r.status_code == 200
    assert r.json()["user"]["id"] == "student-001"


def test_login_wrong_password_401(api_client):
    r = api_client.post("/api/auth/login", json={"username": "student-001", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user_401(api_client):
    r = api_client.post("/api/auth/login", json={"username": "nobody", "password": "123456"})
    assert r.status_code == 401
