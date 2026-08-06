"""M6 API 工单测试：RBAC 数据过滤（student 自己的 / staff 按部门 / admin 全量）+ 操作接口。"""

from datetime import UTC, datetime, timedelta

from campus_desk.db.models import Ticket
from tests.test_api_auth import _login


def _auth(client, username):
    return {"Authorization": f"Bearer {_login(client, username)['token']}"}


def _seed_tickets(factory, specs):
    """预插工单：specs = [(user_id, dept, status, category), ...]"""
    with factory() as session, session.begin():
        for i, (user_id, dept, status, category) in enumerate(specs):
            session.add(
                Ticket(
                    user_id=user_id,
                    ticket_type="repair",
                    description=f"测试单{i}",
                    contact="李华",
                    category=category,
                    priority="P2",
                    status=status,
                    dept=dept,
                    created_at=datetime.now(UTC) - timedelta(minutes=i),
                )
            )


class TestListFilter:
    def test_student_sees_only_own(self, api_client, db_session_factory):
        _seed_tickets(
            db_session_factory,
            [
                ("student-001", "后勤", "ASSIGNED", "水电"),
                ("student-002", "后勤", "ASSIGNED", "水电"),
                ("admin-001", None, "SUBMITTED", "其他"),
            ],
        )
        body = api_client.get(
            "/api/tickets", headers=_auth(api_client, "student-001")
        ).json()
        assert body["total"] == 1
        # 归属校验走详情接口（summary 不含 user_id，防越权信息泄露）
        detail = api_client.get(
            f"/api/tickets/{body['items'][0]['id']}", headers=_auth(api_client, "student-001")
        ).json()
        assert detail["user_id"] == "student-001"

    def test_staff_sees_own_dept_only(self, api_client, db_session_factory):
        _seed_tickets(
            db_session_factory,
            [
                ("student-001", "后勤", "ASSIGNED", "水电"),
                ("student-002", "信息中心", "ASSIGNED", "网络"),
            ],
        )
        body = api_client.get(
            "/api/tickets", headers=_auth(api_client, "staff-001")
        ).json()  # staff-001 dept=后勤
        assert body["total"] == 1

    def test_admin_sees_all(self, api_client, db_session_factory):
        _seed_tickets(
            db_session_factory,
            [
                ("student-001", "后勤", "ASSIGNED", "水电"),
                ("student-002", "信息中心", "ASSIGNED", "网络"),
                ("admin-001", None, "SUBMITTED", "其他"),
            ],
        )
        body = api_client.get("/api/tickets", headers=_auth(api_client, "admin-001")).json()
        assert body["total"] == 3

    def test_status_filter(self, api_client, db_session_factory):
        _seed_tickets(
            db_session_factory,
            [("student-001", "后勤", "ASSIGNED", "水电"), ("student-001", "后勤", "CLOSED", "水电")],
        )
        body = api_client.get(
            "/api/tickets?status=CLOSED", headers=_auth(api_client, "student-001")
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "CLOSED"


class TestDetailPermission:
    def test_student_cannot_read_other_ticket_404(self, api_client, db_session_factory):
        _seed_tickets(db_session_factory, [("student-002", "后勤", "ASSIGNED", "水电")])
        r = api_client.get(
            "/api/tickets/1", headers=_auth(api_client, "student-001")
        )
        assert r.status_code == 404  # 越权与不存在统一 404（防枚举）

    def test_student_can_read_own_detail(self, api_client, db_session_factory):
        _seed_tickets(db_session_factory, [("student-001", "后勤", "ASSIGNED", "水电")])
        r = api_client.get("/api/tickets/1", headers=_auth(api_client, "student-001"))
        assert r.status_code == 200
        body = r.json()
        assert body["user_id"] == "student-001"
        assert body["contact"] == "李华"
        assert body["logs_count"] >= 0

    def test_unknown_ticket_404(self, api_client):
        r = api_client.get("/api/tickets/999", headers=_auth(api_client, "admin-001"))
        assert r.status_code == 404


class TestActions:
    def test_owner_verify_and_cancel(self, api_client, db_session_factory):
        _seed_tickets(db_session_factory, [("student-001", "后勤", "ASSIGNED", "水电")])
        h = _auth(api_client, "student-001")
        # 撤回：ASSIGNED → CANCELLED
        r = api_client.post("/api/tickets/1/cancel", headers=h)
        assert r.status_code == 200
        # 验收对 CANCELLED 非法 → 400（状态机校验兜底）
        r2 = api_client.post("/api/tickets/1/verify", headers=h)
        assert r2.status_code == 400

    def test_non_owner_action_404(self, api_client, db_session_factory):
        _seed_tickets(db_session_factory, [("student-002", "后勤", "ASSIGNED", "水电")])
        r = api_client.post(
            "/api/tickets/1/cancel", headers=_auth(api_client, "student-001")
        )
        assert r.status_code == 404

    def test_staff_cannot_verify_student_ticket_403(self, api_client, db_session_factory):
        """M6 验收坑：staff 能看见本部门学生单，但不得代替验收/撤回（owner 校验）。"""
        _seed_tickets(db_session_factory, [("student-001", "后勤", "PENDING_VERIFY", "水电")])
        h = _auth(api_client, "staff-001")  # dept=后勤，可见该单
        r1 = api_client.post("/api/tickets/1/verify", headers=h)
        assert r1.status_code == 403
        r2 = api_client.post("/api/tickets/1/cancel", headers=h)
        assert r2.status_code == 403

    def test_admin_assign(self, api_client, db_session_factory):
        _seed_tickets(db_session_factory, [("student-001", None, "SUBMITTED", "水电")])
        r = api_client.post(
            "/api/admin/tickets/1/assign",
            json={"repairman_id": "rm-001", "dept": "后勤"},
            headers=_auth(api_client, "admin-001"),
        )
        assert r.status_code == 200
        r2 = api_client.get("/api/tickets/1", headers=_auth(api_client, "admin-001"))
        assert r2.json()["status"] == "ASSIGNED"
        assert r2.json()["repairman_id"] == "rm-001"

    def test_assign_requires_repairman_or_dept(self, api_client, db_session_factory):
        _seed_tickets(db_session_factory, [("student-001", None, "SUBMITTED", "水电")])
        r = api_client.post(
            "/api/admin/tickets/1/assign",
            json={},
            headers=_auth(api_client, "admin-001"),
        )
        assert r.status_code == 400


class TestStaffList:
    def test_staff_list_returns_repairmen(self, api_client):
        """派单候选 = repairmen 表（tickets.repairman_id 外键目标），非 users 账号表。

        M6 验收坑：原实现返回 users.id（staff-001），派单写库违反外键 → 500。
        """
        body = api_client.get(
            "/api/admin/staff", headers=_auth(api_client, "admin-001")
        ).json()
        ids = {s["id"] for s in body}
        names = {s["name"] for s in body}
        assert "rm-001" in ids  # 陈师傅·后勤·水电
        assert "rm-005" in ids  # 赵工·信息中心·网络
        assert "陈师傅" in names and "赵工" in names
        assert "staff-001" not in ids  # users 账号不进派单候选
        assert all(s["dept"] in ("后勤", "信息中心") for s in body)
        assert all(s["on_duty"] for s in body)  # 不在岗（rm-008）不参与派单

    def test_assign_rejects_unknown_repairman(self, api_client, db_session_factory):
        """防御：repairman_id 不存在 → 400 明确报错（而非外键 500）。"""
        _seed_tickets(db_session_factory, [("student-001", None, "SUBMITTED", "水电")])
        r = api_client.post(
            "/api/admin/tickets/1/assign",
            json={"repairman_id": "staff-001", "dept": "后勤"},  # users 账号 id ≠ repairmen id
            headers=_auth(api_client, "admin-001"),
        )
        assert r.status_code == 400
