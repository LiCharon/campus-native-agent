"""M6 API 对话测试：建单派单 / 追问 resume / 新 thread 新会话 / user_id 来自 token。"""

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.repair.classify import ClassificationResult
from campus_desk.repair.drafting import DraftExtract
from campus_desk.repair.graph import build_repair_graph
from tests.conftest import FakeFieldExtractor, FakeRepairClassifier
from tests.test_api_auth import _login


def _auth(client, username="student-001"):
    return {"Authorization": f"Bearer {_login(client, username)['token']}"}


class TestChat:
    def test_repair_creates_ticket(self, api_client):
        r = api_client.post(
            "/api/chat",
            json={"thread_id": "chat-1", "msg": "3号楼502灯坏了，李华"},
            headers=_auth(api_client),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["route"] == "repair"
        assert body["ticket_id"] == 1
        assert body["ticket_status"] == "ASSIGNED"
        assert body["reply"]
        assert body["tool_calls"]

    def test_new_thread_new_session(self, api_client):
        """不同 thread_id 互不影响（同一用户两个独立会话）。"""
        h = _auth(api_client)
        r1 = api_client.post(
            "/api/chat", json={"thread_id": "a", "msg": "3号楼502灯坏了，李华"}, headers=h
        )
        r2 = api_client.post(
            "/api/chat", json={"thread_id": "b", "msg": "3号楼502灯坏了，李华"}, headers=h
        )
        assert r1.json()["ticket_id"] == 1
        assert r2.json()["ticket_id"] == 2

    def test_resume_after_question(self, api_client):
        """追问挂起 → 第二轮 resume 续跑（第一轮缺楼栋先追问，补楼栋后建单）。"""
        # 替换该用户 bundle 的 repair 图为"先追问后建单"版本（序列消费）
        registry = api_client.app.state.registry
        session_factory = api_client.app.state.session_factory
        original_factory = registry._bundle_factory

        def _bundle(user_id):
            bundle = original_factory(user_id)
            repair = build_repair_graph(
                session_factory,
                extractor=FakeFieldExtractor(
                    sequence=[
                        DraftExtract(description="", building=None, room=None, contact=None),
                        DraftExtract(description="", building="3号楼", room="502", contact="李华"),
                    ]
                ),
                classifier=FakeRepairClassifier(
                    default=ClassificationResult(category="水电", priority="P2", confidence=0.9)
                ),
                checkpointer=InMemorySaver(),
                user_id=user_id,
                actor=user_id,
            )
            return type(bundle)(
                entry=bundle.entry,
                repair=repair,
                consult=bundle.consult,
                quality=bundle.quality,
                complaint=bundle.complaint,
            )

        registry._bundle_factory = _bundle
        h = _auth(api_client)
        r1 = api_client.post("/api/chat", json={"thread_id": "c", "msg": "502灯坏了"}, headers=h)
        assert r1.json()["pending_question"], "第一轮应追问缺项"
        r2 = api_client.post("/api/chat", json={"thread_id": "c", "msg": "3号楼"}, headers=h)
        assert r2.json()["ticket_id"] == 1, "resume 后应建单"
        assert r2.json()["pending_question"] is None

    def test_user_id_from_token_not_body(self, api_client):
        """user_id 取自 JWT 绝不信请求体：student-002 登录建的单归属 student-002。"""
        from campus_desk.db.models import Ticket

        r = api_client.post(
            "/api/chat",
            json={"thread_id": "d", "msg": "3号楼502灯坏了，李华"},
            headers=_auth(api_client, "student-002"),
        )
        assert r.status_code == 200
        with api_client.app.state.session_factory() as session:
            ticket = session.query(Ticket).one()
        assert ticket.user_id == "student-002"
