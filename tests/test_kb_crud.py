"""M9 知识条目增改删 API 测试：CRUD + 审计 + 向量同步降级 + RBAC 门控 + adopt 接同步。

不依赖网络/模型：Qdrant 强制不可用（is_available=False），fastembed 注入假 512 维向量；
嵌入不可用场景单独验证 CRUD 不阻断（dense_vector 留空，走 Tier3 关键词兜底）。
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from campus_desk.db.models import AuditLog, KnowledgeEntry
from campus_desk.knowledge import embeddings, vector_store

_VALID = {
    "domain": "图书馆",
    "type": "info",
    "question": "图书馆暑假开放时间",
    "keywords": "图书馆,暑假,开放",
    "answer": "暑假 8:00-22:00",
}


def _login(client, username="admin-001", password="123456"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"login {username}: {r.status_code} {r.text}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed_bad_case(client):
    stu = _login(client, "student-001")
    # M15A-⑦：feedback 校验 thread_id 归属，必须用真实会话
    r_thread = client.post("/api/sessions", headers=stu)
    assert r_thread.status_code == 200
    r = client.post(
        "/api/feedback/bad-case",
        headers=stu,
        json={
            "thread_id": r_thread.json()["thread_id"],
            "question": "食堂在哪？",
            "reply": "超出范围",
            "note": "",
        },
    )
    assert r.status_code == 200
    return r.json()["id"]


@pytest.fixture
def kb_offline(monkeypatch):
    """强制检索层降级：Qdrant 不可用 + 假 512 维稠密向量（测试离线可跑）。"""
    monkeypatch.setattr(vector_store, "is_available", lambda: False)

    def _fake_embed(texts):
        v = np.zeros(512, dtype=np.float32)
        v[0] = 1.0
        return np.repeat(v[None, :], len(texts), axis=0)

    monkeypatch.setattr(embeddings, "embed_dense", _fake_embed)


def _audit_actions(session_factory, action):
    with session_factory() as s, s.begin():
        return [a.id for a in s.query(AuditLog).filter_by(action=action).all()]


def test_create_knowledge_writes_row_vector_audit(api_client, db_session_factory, kb_offline):
    admin = _login(api_client)
    r = api_client.post("/api/admin/knowledge", headers=admin, json=_VALID)
    assert r.status_code == 200, r.text
    kid = r.json()["id"]
    assert r.json()["question"] == _VALID["question"]
    # 列表可见
    lst = api_client.get("/api/admin/knowledge", headers=admin).json()["items"]
    assert any(k["id"] == kid for k in lst)
    # MySQL 稠密向量已写（Tier2 兜底）
    with db_session_factory() as s, s.begin():
        row = s.get(KnowledgeEntry, kid)
        assert row is not None
        assert row.dense_vector is not None
        assert len(json.loads(row.dense_vector)) == 512
    # 审计
    assert _audit_actions(db_session_factory, "kb_create")


def test_update_knowledge_changes_fields_and_resync(api_client, db_session_factory, kb_offline):
    admin = _login(api_client)
    kid = api_client.post("/api/admin/knowledge", headers=admin, json=_VALID).json()["id"]
    payload = dict(
        _VALID, question="图书馆暑假开放时间（更新）", type="process", answer="暑假 9:00-21:00"
    )
    r = api_client.put(f"/api/admin/knowledge/{kid}", headers=admin, json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["question"] == payload["question"]
    with db_session_factory() as s, s.begin():
        row = s.get(KnowledgeEntry, kid)
        assert row.question == payload["question"]
        assert row.type == "process"
        assert row.answer == payload["answer"]
        assert row.dense_vector is not None  # 更新后重新同步
    assert _audit_actions(db_session_factory, "kb_update")


def test_delete_knowledge_removes_row_and_404(api_client, db_session_factory, kb_offline):
    admin = _login(api_client)
    kid = api_client.post("/api/admin/knowledge", headers=admin, json=_VALID).json()["id"]
    r = api_client.delete(f"/api/admin/knowledge/{kid}", headers=admin)
    assert r.status_code == 200, r.text
    with db_session_factory() as s, s.begin():
        assert s.get(KnowledgeEntry, kid) is None
    # 重复删除 → 404
    assert api_client.delete(f"/api/admin/knowledge/{kid}", headers=admin).status_code == 404
    assert _audit_actions(db_session_factory, "kb_delete")


def test_knowledge_write_requires_kb_review(api_client, kb_offline):
    payload = dict(_VALID)
    stu = _login(api_client, "student-001")
    assert api_client.post("/api/admin/knowledge", headers=stu, json=payload).status_code == 403
    cs = _login(api_client, "cs-001")
    assert api_client.post("/api/admin/knowledge", headers=cs, json=payload).status_code == 403


def test_create_ok_when_embedding_unavailable(api_client, db_session_factory, monkeypatch):
    """嵌入不可用（离线/未装 fastembed）→ CRUD 不阻断，dense_vector 留空走关键词兜底。"""
    monkeypatch.setattr(vector_store, "is_available", lambda: False)

    def _raise(*_a, **_k):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_dense", _raise)
    admin = _login(api_client)
    r = api_client.post("/api/admin/knowledge", headers=admin, json=_VALID)
    assert r.status_code == 200, r.text
    with db_session_factory() as s, s.begin():
        row = s.get(KnowledgeEntry, r.json()["id"])
        assert row.dense_vector is None


def test_adopt_writes_dense_vector(api_client, db_session_factory, kb_offline):
    bid = _seed_bad_case(api_client)
    admin = _login(api_client)
    r = api_client.post(
        f"/api/admin/reviews/bad_cases/{bid}/adopt",
        headers=admin,
        json={"domain": "生活服务", "type": "info", "keywords": "食堂,位置", "answer": "朝晖校区食堂在……"},
    )
    assert r.status_code == 200, r.text
    with db_session_factory() as s, s.begin():
        rows = s.query(KnowledgeEntry).filter_by(question="食堂在哪？").all()
        assert rows, "adopt 应补入知识库"
        assert rows[0].dense_vector is not None, "adopt 补入的条目应同步稠密向量"
