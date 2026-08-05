"""种子数据测试：幂等（跑两遍数量不变）+ 数量与关键字段约束。"""

from campus_desk.db.models import Account, Announcement, Dorm, Faq, Repairman, User
from campus_desk.db.seed import seed_all


def _count(factory, model) -> int:
    with factory() as session:
        return session.query(model).count()


class TestSeedContent:
    def test_seed_loaded(self, db_session_factory):
        """fixture 种子已加载（conftest 里 create_all + seed_all）。"""
        assert _count(db_session_factory, User) == 9
        assert _count(db_session_factory, Repairman) == 8
        assert _count(db_session_factory, Dorm) == 5
        assert _count(db_session_factory, Account) == 3
        assert _count(db_session_factory, Announcement) == 4
        assert _count(db_session_factory, Faq) == 9

    def test_roles_covered(self, db_session_factory):
        with db_session_factory() as session:
            roles = {r[0] for r in session.query(User.role).distinct()}
            assert {"student", "staff", "it_staff", "admin"} == roles

    def test_repairman_off_duty_for_priority_test(self, db_session_factory):
        """含 1 名不在岗维修工（"在岗优先"派单规则测试用）。"""
        with db_session_factory() as session:
            off = session.query(Repairman).filter(Repairman.on_duty.is_(False)).all()
            assert len(off) == 1
            assert off[0].dept == "后勤" and off[0].trade == "水电"

    def test_faq_categories(self, db_session_factory):
        with db_session_factory() as session:
            cats = {c[0] for c in session.query(Faq.category).distinct()}
            assert {"网络", "教务", "密码", "邮箱"} == cats

    def test_account_three_states(self, db_session_factory):
        """accounts mock 三种状态（query_account_status 分支测试依赖）。"""
        with db_session_factory() as session:
            states = {s[0] for s in session.query(Account.status).distinct()}
            assert {"normal", "overdue", "expired"} == states


class TestSeedIdempotent:
    def test_run_twice_no_duplicates(self, db_session_factory):
        """重跑种子不重复（固定 id upsert，幂等核心）。"""
        before = {
            m: _count(db_session_factory, m)
            for m in (User, Repairman, Dorm, Account, Announcement, Faq)
        }
        counts = seed_all(db_session_factory)
        assert all(v == 0 for v in counts.values()), f"重复入库 {counts}"
        after = {
            m: _count(db_session_factory, m)
            for m in (User, Repairman, Dorm, Account, Announcement, Faq)
        }
        assert before == after

    def test_force_overwrite_updates_fields(self, db_session_factory):
        """force=True 重写字段（测试隔离用）。"""
        with db_session_factory() as session:
            rm = session.get(Repairman, "rm-001")
            rm.on_duty = False
        counts = seed_all(db_session_factory, force=True)
        assert counts["repairmen"] == 8
        with db_session_factory() as session:
            rm = session.get(Repairman, "rm-001")
            assert rm.on_duty is True  # force 重写回种子值
