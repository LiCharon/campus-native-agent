"""M7 API FAQ 管理测试：admin 增删改查闭环 / student·staff 只读（写 403） / 无 token 401。

写接口 = /api/admin/faqs*（require_roles("admin") 门控）；读接口 /api/faqs
任意登录角色可用。缓存一致性（写后 flush）见 test_faq_cache.py。
"""

from tests.test_api_auth import _login

_PAYLOAD = {
    "category": "网络",
    "keywords": "wifi,无线,连不上",
    "question": "宿舍连不上 WiFi 怎么办？",
    "answer": "请先重启路由器，并在校园网认证页面重新登录。",
}


def _auth(client, username):
    return {"Authorization": f"Bearer {_login(client, username)['token']}"}


class TestFaqCrud:
    def test_admin_crud_roundtrip(self, api_client, db_session_factory):
        """admin 增删改查闭环：建 → 列表可见 → 改 → 删 → 列表恢复。"""
        h = _auth(api_client, "admin-001")
        # 初始列表可读（种子 27 条）
        r = api_client.get("/api/faqs", headers=h)
        assert r.status_code == 200
        total0 = r.json()["total"]
        assert total0 > 0
        # 新增
        r = api_client.post("/api/admin/faqs", json=_PAYLOAD, headers=h)
        assert r.status_code == 200
        body = r.json()
        faq_id = body["id"]
        assert body["question"] == _PAYLOAD["question"]
        # 列表可见且总数 +1
        r = api_client.get("/api/faqs", headers=h)
        assert r.status_code == 200
        assert r.json()["total"] == total0 + 1
        assert any(i["id"] == faq_id for i in r.json()["items"])
        # 编辑（全量更新）
        updated = {**_PAYLOAD, "answer": "请重启路由器，并检查认证页面。"}
        r = api_client.put(f"/api/admin/faqs/{faq_id}", json=updated, headers=h)
        assert r.status_code == 200
        assert r.json()["answer"] == updated["answer"]
        r = api_client.get("/api/faqs", headers=h)
        item = next(i for i in r.json()["items"] if i["id"] == faq_id)
        assert item["answer"] == updated["answer"]
        # 删除
        r = api_client.delete(f"/api/admin/faqs/{faq_id}", headers=h)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = api_client.get("/api/faqs", headers=h)
        assert r.json()["total"] == total0
        assert all(i["id"] != faq_id for i in r.json()["items"])

    def test_update_delete_unknown_404(self, api_client):
        """不存在/已删条目 → 404（不写脏数据）。"""
        h = _auth(api_client, "admin-001")
        assert api_client.put("/api/admin/faqs/99999", json=_PAYLOAD, headers=h).status_code == 404
        assert api_client.delete("/api/admin/faqs/99999", headers=h).status_code == 404


class TestFaqPermission:
    def test_student_read_ok_write_403(self, api_client):
        """student：列表可读，增删改全 403。"""
        h = _auth(api_client, "student-001")
        assert api_client.get("/api/faqs", headers=h).status_code == 200
        assert api_client.post("/api/admin/faqs", json=_PAYLOAD, headers=h).status_code == 403
        assert api_client.put("/api/admin/faqs/1", json=_PAYLOAD, headers=h).status_code == 403
        assert api_client.delete("/api/admin/faqs/1", headers=h).status_code == 403

    def test_staff_read_ok_write_403(self, api_client):
        """staff：列表可读，写 403（FAQ 管理仅 admin）。"""
        h = _auth(api_client, "staff-001")
        assert api_client.get("/api/faqs", headers=h).status_code == 200
        assert api_client.post("/api/admin/faqs", json=_PAYLOAD, headers=h).status_code == 403

    def test_it_staff_write_403(self, api_client):
        """it_staff：写 403（FAQ 管理门控仅 admin，M6 管理角色不自动放行）。"""
        h = _auth(api_client, "it-001")
        assert api_client.post("/api/admin/faqs", json=_PAYLOAD, headers=h).status_code == 403

    def test_no_token_401(self, api_client):
        assert api_client.get("/api/faqs").status_code == 401
        assert api_client.post("/api/admin/faqs", json=_PAYLOAD).status_code == 401
