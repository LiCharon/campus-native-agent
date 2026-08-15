"""种子数据测试：幂等（跑两遍数量不变）+ 数量与关键字段约束。

M1-T2 退役 7 表后仅剩 users 种子；M1-T9 重写本文件：
36 条通用知识库（6 领域 × 6 条）+ cs-001 客服账号。
"""

from campus_desk.db.models import KnowledgeEntry, User
from campus_desk.db.seed import seed_all


def _count(factory, model) -> int:
    with factory() as session:
        return session.query(model).count()


class TestSeedContent:
    def test_seed_loaded(self, db_session_factory):
        """fixture 种子已加载（conftest 里 create_all + seed_all）。"""
        assert _count(db_session_factory, User) == 10

    def test_roles_covered(self, db_session_factory):
        with db_session_factory() as session:
            roles = {r[0] for r in session.query(User.role).distinct()}
            assert {"student", "staff", "it_staff", "admin", "cs_staff"} == roles

    def test_seed_creates_36_knowledge_entries(self, db_session_factory):
        """T9：通用知识库恰 36 条（6 领域 × 6 条）。"""
        with db_session_factory() as session:
            assert session.query(KnowledgeEntry).count() == 36

    def test_seed_creates_cs_staff_user(self, db_session_factory):
        """T9：cs-001 客服账号存在且角色为 cs_staff。"""
        with db_session_factory() as session:
            u = session.query(User).filter(User.id == "cs-001").first()
            assert u is not None and u.role == "cs_staff"

    def test_seed_knowledge_types_covered(self, db_session_factory):
        """T9：知识条 type 覆盖 info/process/index 三种组装形态。"""
        with db_session_factory() as session:
            types = {t[0] for t in session.query(KnowledgeEntry.type).distinct()}
            assert types == {"info", "process", "index"}


class TestSeedIdempotent:
    def test_run_twice_no_duplicates(self, db_session_factory):
        """重跑种子不重复（固定 id upsert，幂等核心；T9 覆盖知识表）。"""
        before = {m: _count(db_session_factory, m) for m in (User, KnowledgeEntry)}
        counts = seed_all(db_session_factory)
        assert all(v == 0 for v in counts.values()), f"重复入库 {counts}"
        after = {m: _count(db_session_factory, m) for m in (User, KnowledgeEntry)}
        assert before == after


class TestSeedPassword:
    """M6 登录鉴权：种子用户带可验证的密码哈希（统一演示密码 123456）。"""

    def test_seed_users_have_password_hash(self, db_session_factory):
        from campus_desk.security import verify_password

        with db_session_factory() as session:
            users = session.query(User).all()
        assert len(users) == 10
        for u in users:
            assert u.password_hash, f"{u.id} 缺 password_hash"
            assert verify_password("123456", u.password_hash), f"{u.id} 密码校验失败"

    def test_seed_rerun_keeps_password_verifiable(self, db_session_factory):
        """force 重跑种子（覆盖更新路径）后密码仍可验证。"""
        from campus_desk.security import verify_password

        seed_all(db_session_factory, force=True)
        with db_session_factory() as session:
            u = session.query(User).filter(User.id == "student-001").one()
        assert verify_password("123456", u.password_hash)
