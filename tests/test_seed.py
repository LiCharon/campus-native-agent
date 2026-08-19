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
        assert _count(db_session_factory, User) == 5

    def test_roles_covered(self, db_session_factory):
        with db_session_factory() as session:
            roles = {r[0] for r in session.query(User.role).distinct()}
            assert {"student", "admin", "cs_staff"} == roles

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
        assert len(users) == 5
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


class TestSeedMockTables:
    """M2：empty_rooms / library_seats mock 种子（数量 + 周中/周末两模式）。"""

    def test_seed_creates_mock_tables(self, db_session_factory):
        """M2：empty_rooms 恰 189 行（3 楼×7 天×3 时段×3 间）、library_seats 恰 5 层。"""
        from campus_desk.db.models import EmptyRoom, LibrarySeat

        with db_session_factory() as session:
            assert session.query(EmptyRoom).count() == 189
            assert session.query(LibrarySeat).count() == 5

    def test_seed_mock_weekday_patterns(self, db_session_factory):
        """周中/周末两模式：同一楼栋相同时段，周中与周末的房间集合不同。"""
        from campus_desk.db.models import EmptyRoom

        with db_session_factory() as session:
            wk = {
                r.room
                for r in session.query(EmptyRoom).filter(
                    EmptyRoom.building == "3号楼",
                    EmptyRoom.weekday == 3,
                    EmptyRoom.period == "下午",
                )
            }
            wkend = {
                r.room
                for r in session.query(EmptyRoom).filter(
                    EmptyRoom.building == "3号楼",
                    EmptyRoom.weekday == 6,
                    EmptyRoom.period == "下午",
                )
            }
        assert wk == {"301", "305", "308"}
        assert wkend == {"302", "306", "309"}

    def test_seed_creates_fc_mock_tables(self, db_session_factory):
        """FC 扩展：10 张新 mock 表种子数量（3 学生 × 18 周 × 5 时段=270 等）。"""
        from campus_desk.db.models import (
            AcademicCalendar,
            Announcement,
            CardBalance,
            DormPower,
            ExamSchedule,
            ExamScore,
            LibraryBorrow,
            LostItem,
            ShuttleSchedule,
            Timetable,
        )

        with db_session_factory() as session:
            assert session.query(Timetable).count() == 270
            assert session.query(ExamScore).count() == 36
            assert session.query(ExamSchedule).count() == 15
            assert session.query(LibraryBorrow).count() == 9
            assert session.query(CardBalance).count() == 3
            assert session.query(DormPower).count() == 15
            assert session.query(LostItem).count() == 10
            assert session.query(ShuttleSchedule).count() == 24
            assert session.query(AcademicCalendar).count() == 37
            assert session.query(Announcement).count() == 8

    def test_seed_fc_mock_covers_all_students(self, db_session_factory):
        """课表/成绩/余额覆盖 3 个学生；校历覆盖 2026-2027-1 第 19 周考试周。"""
        from campus_desk.db.models import AcademicCalendar, CardBalance, Timetable

        with db_session_factory() as session:
            students = {r[0] for r in session.query(Timetable.student_no).distinct()}
            assert students == {"2024001", "2024002", "2024003"}
            assert session.query(CardBalance).count() == 3
            exam_week = (
                session.query(AcademicCalendar)
                .filter(AcademicCalendar.term == "2026-2027-1", AcademicCalendar.label == "考试周")
                .all()
            )
            assert len(exam_week) == 1 and exam_week[0].week == 19

    def test_seed_fc_idempotent(self, db_session_factory):
        """FC mock 表重跑种子不重复（幂等键 upsert）。"""
        from campus_desk.db.models import (
            AcademicCalendar,
            Announcement,
            CardBalance,
            DormPower,
            ExamSchedule,
            ExamScore,
            LibraryBorrow,
            LostItem,
            ShuttleSchedule,
            Timetable,
        )

        models = (
            Timetable,
            ExamScore,
            ExamSchedule,
            LibraryBorrow,
            CardBalance,
            DormPower,
            LostItem,
            ShuttleSchedule,
            AcademicCalendar,
            Announcement,
        )
        before = {m.__tablename__: _count(db_session_factory, m) for m in models}
        counts = seed_all(db_session_factory)
        assert all(v == 0 for v in counts.values()), f"重复入库 {counts}"
        after = {m.__tablename__: _count(db_session_factory, m) for m in models}
        assert before == after
