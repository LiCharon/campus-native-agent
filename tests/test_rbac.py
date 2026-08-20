"""M6-ZJUT RBAC 三表测试（TDD 分阶段追加）：

- Step 1：三表种子（数量/映射/幂等）
- Step 3：perms 查库化函数（get_role_perms / effective_perms_from_db）
- Step 4：login JWT claims 与 DB 驱动权限变更
- Step 5：只读接口鉴权与校验查库
"""

from campus_desk.db.models import Permission, Role, RolePermission
from campus_desk.db.seed import seed_all


def _count(factory, model) -> int:
    with factory() as session:
        return session.query(model).count()


class TestRbacSeed:
    """M6 Step 1：三表种子数量 / 映射 / 幂等。"""

    def test_seed_three_tables(self, db_session_factory):
        """三表种子数量：roles 3 / permissions 6 / role_permissions 9。"""
        assert _count(db_session_factory, Role) == 3
        assert _count(db_session_factory, Permission) == 6
        assert _count(db_session_factory, RolePermission) == 9

    def test_seed_role_permission_mapping(self, db_session_factory):
        """按 role_id 分组断言映射与现 ROLE_PERMS 一致（种子=硬编码映射的入库版）。"""
        from campus_desk.perms import ROLE_PERMS

        with db_session_factory() as session:
            rows = session.query(RolePermission).all()
        by_role: dict[str, list[str]] = {}
        for rp in rows:
            by_role.setdefault(rp.role_id, []).append(rp.permission_id)
        assert {r: sorted(v) for r, v in by_role.items()} == {
            r: sorted(ps) for r, ps in ROLE_PERMS.items()
        }

    def test_seed_rbac_idempotent(self, db_session_factory):
        """seed_all 重跑后三表计数不变（写入计数为 0）。"""
        models = (Role, Permission, RolePermission)
        before = {m.__tablename__: _count(db_session_factory, m) for m in models}
        counts = seed_all(db_session_factory)
        assert all(v == 0 for v in counts.values()), f"重复入库 {counts}"
        after = {m.__tablename__: _count(db_session_factory, m) for m in models}
        assert before == after


class TestRbacPermsFromDb:
    """M6 Step 3：perms 查库化函数（get_role_perms / effective_perms_from_db）。"""

    def test_get_role_perms_from_db(self, db_session_factory):
        """查库取角色默认权限：student→[chat]、cs_staff→[chat,cs_workbench]、admin→6 位全量。"""
        from campus_desk.perms import get_role_perms

        with db_session_factory() as session:
            assert get_role_perms(session, "student") == ["chat"]
            assert get_role_perms(session, "cs_staff") == ["chat", "cs_workbench"]
            assert get_role_perms(session, "admin") == [
                "chat",
                "cs_workbench",
                "kb_review",
                "user_mgmt",
                "view_logs",
                "view_stats",
            ]

    def test_get_role_perms_unknown_role_fallback(self, db_session_factory):
        """未知角色（不在 roles 表）回退 ROLE_PERMS 兜底 → ["chat"]，不崩。"""
        from campus_desk.perms import get_role_perms

        with db_session_factory() as session:
            assert get_role_perms(session, "ghost") == ["chat"]

    def test_effective_perms_from_db_union(self, db_session_factory):
        """最终权限 = 角色默认(查库) ∪ 附加位（顺序稳定 + 去重）。"""
        from campus_desk.db.models import User
        from campus_desk.perms import effective_perms_from_db

        with db_session_factory() as session:
            u = session.query(User).filter(User.id == "student-001").one()
            u.permissions = "kb_review"
            session.commit()
            assert effective_perms_from_db(session, "student", "kb_review") == [
                "chat",
                "kb_review",
            ]

    def test_effective_perms_db_reflects_role_table(self, db_session_factory):
        """运行时以 DB 为准：给 RolePermission 加 (student, view_stats) 后查库版立即反映。"""
        from campus_desk.perms import effective_perms_from_db

        with db_session_factory() as session:
            session.add(RolePermission(role_id="student", permission_id="view_stats"))
            session.commit()
            assert "view_stats" in effective_perms_from_db(session, "student", "")


class TestRbacLogin:
    """M6 Step 4：login 权限查库化（JWT claims 携带最终并集，DB 变更重登生效）。

    顺序断言：DB 版按 permission_id 字母序（chat < cs_workbench < kb_review <
    user_mgmt < view_logs < view_stats），与硬编码 ROLE_PERMS 的 view_stats 在
    user_mgmt 前的顺序不同——靠顺序区分"查库"与"硬编码"。
    """

    def test_login_jwt_claims_perms(self, api_client, db_session_factory):
        from campus_desk.security import decode_access_token

        # admin-001 → 6 位全量（DB 字母序）
        r = api_client.post("/api/auth/login", json={"username": "admin-001", "password": "123456"})
        assert r.status_code == 200
        claims = decode_access_token(r.json()["token"])
        assert claims["perms"] == [
            "chat",
            "cs_workbench",
            "kb_review",
            "user_mgmt",
            "view_logs",
            "view_stats",
        ]

        # student-001 → 仅 chat
        r = api_client.post(
            "/api/auth/login", json={"username": "student-001", "password": "123456"}
        )
        assert r.status_code == 200
        claims = decode_access_token(r.json()["token"])
        assert claims["perms"] == ["chat"]

    def test_login_reflects_db_change_after_relogin(self, api_client, db_session_factory):
        """写库给 cs_staff 加 kb_review → cs-001 重登后 claims 含 kb_review（DB 驱动）。"""
        from campus_desk.security import decode_access_token

        with db_session_factory() as session:
            session.add(RolePermission(role_id="cs_staff", permission_id="kb_review"))
            session.commit()

        r = api_client.post("/api/auth/login", json={"username": "cs-001", "password": "123456"})
        assert r.status_code == 200
        claims = decode_access_token(r.json()["token"])
        assert "kb_review" in claims["perms"]


class TestRbacEndpoints:
    """M6 Step 5：只读接口鉴权 + _validate_permissions 查库化（DB 新增角色/权限可授予）。"""

    @staticmethod
    def _login(client, username, password="123456"):
        r = client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def test_roles_endpoint_authz(self, api_client):
        """GET /api/admin/roles：未登录 401 / student 403 / admin 200 且 3 项。"""
        assert api_client.get("/api/admin/roles").status_code == 401
        student = self._login(api_client, "student-001")
        assert api_client.get("/api/admin/roles", headers=student).status_code == 403
        admin = self._login(api_client, "admin-001")
        r = api_client.get("/api/admin/roles", headers=admin)
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 3
        assert {i["id"] for i in items} == {"student", "cs_staff", "admin"}
        assert all("name" in i for i in items)

    def test_permissions_endpoint_authz(self, api_client):
        """GET /api/admin/permissions：student 403 / admin 200 且 6 项含 chat。"""
        student = self._login(api_client, "student-001")
        assert api_client.get("/api/admin/permissions", headers=student).status_code == 403
        admin = self._login(api_client, "admin-001")
        r = api_client.get("/api/admin/permissions", headers=admin)
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 6
        assert {"id": "chat", "name": "对话服务"} in items

    def test_validate_perm_against_db(self, api_client, db_session_factory):
        """校验以 DB 为准：permissions/roles 表新增行后，create_user 可授予/使用。"""
        from campus_desk.db.models import Permission, Role

        with db_session_factory() as session:
            session.add(Permission(id="audit_export", name="导出审计"))
            session.add(Role(id="operator", name="运营"))
            session.commit()

        admin = self._login(api_client, "admin-001")
        r = api_client.post(
            "/api/admin/users",
            headers=admin,
            json={
                "id": "op-001",
                "name": "运营员",
                "role": "operator",
                "password": "123456",
                "permissions": ["audit_export"],
            },
        )
        assert r.status_code == 200
