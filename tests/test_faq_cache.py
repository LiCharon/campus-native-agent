"""M7 FAQ 缓存测试：不依赖真 Redis（单测替身 FakeRedis + 必拒端口）全绿。

覆盖三条路径：
1. 未配 REDIS_URL → 缓存关闭，search_faq 直查 DB 正常出结果
2. REDIS_URL 配了但连不上 → 降级直查不抛异常，且进入冷却期（不反复重连）
3. 缓存命中 → 不查库（清空 DB 仍出结果）；管理页写操作后缓存被失效（重查回填）
"""

from campus_desk import faq_cache
from campus_desk.config import settings
from campus_desk.db.models import Faq
from campus_desk.tools.consult_tools import create_consult_tools
from tests.test_api_faqs import _PAYLOAD, _auth


class FakeRedis:
    """dict 版内存 redis 替身（get/set/delete 签名对齐 redis-py，单测用）。"""

    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)
        return 1


def _enable_cache(monkeypatch, client=None):
    """启用缓存并把 _client 换成替身/真连接（测试结束后 monkeypatch 自动还原）。"""
    monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0")
    monkeypatch.setattr(faq_cache, "_client", client)
    monkeypatch.setattr(faq_cache, "_unavailable_until", 0.0)


def _consult_tools(factory):
    return {t.name: t for t in create_consult_tools(factory)}


class TestDegradation:
    def test_no_redis_url_queries_db(self, db_session_factory, monkeypatch):
        """未配 REDIS_URL（本机常态）：缓存关闭，直查 DB 正常出结果。"""
        monkeypatch.setattr(faq_cache, "_client", None)
        monkeypatch.setattr(faq_cache, "_unavailable_until", 0.0)
        out = _consult_tools(db_session_factory)["search_faq"].func("忘记密码")
        assert "密码" in out

    def test_connection_failure_degrades(self, db_session_factory, monkeypatch):
        """REDIS_URL 配了但连不上（端口 1 必拒）：降级直查，不抛异常、进入冷却期。"""
        monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
        monkeypatch.setattr(faq_cache, "_client", None)
        monkeypatch.setattr(faq_cache, "_unavailable_until", 0.0)
        out = _consult_tools(db_session_factory)["search_faq"].func("忘记密码")
        assert "密码" in out
        assert faq_cache._client is None  # 未建立连接
        assert faq_cache._unavailable_until > 0.0  # 已进入冷却期（不反复重连）
        # 冷却期内再调仍直查正常
        out2 = _consult_tools(db_session_factory)["search_faq"].func("选课")
        assert "选课" in out2


class TestCacheHit:
    def test_hit_skips_db(self, db_session_factory, monkeypatch):
        """缓存命中后不查库：清空 DB 表，search_faq 仍返回缓存结果。"""
        fake = FakeRedis()
        _enable_cache(monkeypatch, client=fake)
        tools = _consult_tools(db_session_factory)
        # 第一次：缓存未命中 → 查库并回填
        out1 = tools["search_faq"].func("忘记密码")
        assert "密码" in out1
        assert faq_cache._KEY in fake.data  # 已回填
        # 清空 DB 后再查：命中缓存仍出结果（证明走了缓存没走 DB）
        with db_session_factory() as session, session.begin():
            session.query(Faq).delete()
        out2 = tools["search_faq"].func("忘记密码")
        assert "密码" in out2

    def test_cache_then_miss_returns_db(self, db_session_factory, monkeypatch):
        """缓存命中一次后失效（flush）→ 重新查库，新增条目可见。"""
        fake = FakeRedis()
        _enable_cache(monkeypatch, client=fake)
        tools = _consult_tools(db_session_factory)
        tools["search_faq"].func("忘记密码")
        assert fake.data  # 缓存非空
        faq_cache.flush_faqs()
        assert not fake.data  # flush 后清空
        out = tools["search_faq"].func("忘记密码")
        assert "密码" in out  # 重新查库仍正常


class TestAdminWriteFlushes:
    def test_admin_write_flushes_cache(self, api_client, db_session_factory, monkeypatch):
        """管理页写操作（POST）后缓存被清空；再读重新回填且新条目立即可见。"""
        fake = FakeRedis()
        _enable_cache(monkeypatch, client=fake)
        h = _auth(api_client, "admin-001")
        # 先读一次 → 回填缓存
        api_client.get("/api/faqs", headers=h)
        assert fake.data
        # 新增 → 缓存被 flush
        r = api_client.post("/api/admin/faqs", json=_PAYLOAD, headers=h)
        assert r.status_code == 200
        assert not fake.data  # 写后缓存失效
        # 再读 → 重新回填，且新条目在（缓存的旧数据没挡住新数据）
        body = api_client.get("/api/faqs", headers=h).json()
        assert any(i["id"] == r.json()["id"] for i in body["items"])
        assert fake.data  # 重新回填

    def test_update_and_delete_flush(self, api_client, db_session_factory, monkeypatch):
        """PUT/DELETE 同样失效缓存。"""
        fake = FakeRedis()
        _enable_cache(monkeypatch, client=fake)
        h = _auth(api_client, "admin-001")
        faq_id = api_client.post("/api/admin/faqs", json=_PAYLOAD, headers=h).json()["id"]
        api_client.get("/api/faqs", headers=h)  # 回填
        assert fake.data
        api_client.put(f"/api/admin/faqs/{faq_id}", json=_PAYLOAD, headers=h)
        assert not fake.data
        api_client.get("/api/faqs", headers=h)
        assert fake.data
        api_client.delete(f"/api/admin/faqs/{faq_id}", headers=h)
        assert not fake.data
