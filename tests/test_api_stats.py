"""M6 API 看板聚合测试：预插已知分布，断言 total/by_status/by_priority/by_category 数字。"""

from datetime import UTC, datetime

from campus_desk.db.models import Ticket
from tests.test_api_auth import _login


def _auth(client, username="admin-001"):
    return {"Authorization": f"Bearer {_login(client, username)['token']}"}


def _seed(factory):
    specs = [
        ("student-001", "后勤", "ASSIGNED", "水电", "P2"),
        ("student-001", "后勤", "CLOSED", "水电", "P1"),
        ("student-002", "信息中心", "ASSIGNED", "网络", "P2"),
        ("admin-001", None, "SUBMITTED", "其他", "P3"),
    ]
    with factory() as session, session.begin():
        for i, (uid, dept, status, category, priority) in enumerate(specs):
            session.add(
                Ticket(
                    user_id=uid,
                    ticket_type="repair",
                    description=f"统计单{i}",
                    contact="李华",
                    category=category,
                    priority=priority,
                    status=status,
                    dept=dept,
                    created_at=datetime.now(UTC),
                )
            )


class TestDashboard:
    def test_admin_full_stats(self, api_client, db_session_factory):
        _seed(db_session_factory)
        body = api_client.get("/api/dashboard", headers=_auth(api_client, "admin-001")).json()
        assert body["total"] == 4
        assert body["by_status"] == {"ASSIGNED": 2, "CLOSED": 1, "SUBMITTED": 1}
        assert body["by_priority"] == {"P1": 1, "P2": 2, "P3": 1}
        assert body["by_category"] == {"水电": 2, "网络": 1, "其他": 1}

    def test_staff_stats_scoped_by_dept(self, api_client, db_session_factory):
        """staff 看板按部门过滤（后勤只看到后勤单）。"""
        _seed(db_session_factory)
        body = api_client.get(
            "/api/dashboard", headers=_auth(api_client, "staff-001")
        ).json()  # staff-001 dept=后勤
        assert body["total"] == 2
        assert body["by_status"] == {"ASSIGNED": 1, "CLOSED": 1}
        assert body["by_category"] == {"水电": 2}

    def test_student_forbidden(self, api_client, db_session_factory):
        _seed(db_session_factory)
        r = api_client.get("/api/dashboard", headers=_auth(api_client, "student-001"))
        assert r.status_code == 403
