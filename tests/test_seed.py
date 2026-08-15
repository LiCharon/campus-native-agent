"""种子数据测试：幂等（跑两遍数量不变）+ 数量与关键字段约束。

M1-T2 退役 7 表后仅剩 users 种子；tickets/repairmen/dorms/accounts/
announcements/faq 断言已删（M1-T9 重写本文件：36 条知识 + cs-001）。
"""

from campus_desk.db.models import User
from campus_desk.db.seed import seed_all


def _count(factory, model) -> int:
    with factory() as session:
        return session.query(model).count()


class TestSeedContent:
    def test_seed_loaded(self, db_session_factory):
        """fixture 种子已加载（conftest 里 create_all + seed_all）。"""
        assert _count(db_session_factory, User) == 9

    def test_roles_covered(self, db_session_factory):
        with db_session_factory() as session:
            roles = {r[0] for r in session.query(User.role).distinct()}
            assert {"student", "staff", "it_staff", "admin"} == roles


class TestSeedIdempotent:
    def test_run_twice_no_duplicates(self, db_session_factory):
        """重跑种子不重复（固定 id upsert，幂等核心）。"""
        before = {m: _count(db_session_factory, m) for m in (User,)}
        counts = seed_all(db_session_factory)
        assert all(v == 0 for v in counts.values()), f"重复入库 {counts}"
        after = {m: _count(db_session_factory, m) for m in (User,)}
        assert before == after


class TestSeedPassword:
    """M6 登录鉴权：种子用户带可验证的密码哈希（统一演示密码 123456）。"""

    def test_seed_users_have_password_hash(self, db_session_factory):
        from campus_desk.security import verify_password

        with db_session_factory() as session:
            users = session.query(User).all()
        assert len(users) == 9
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
