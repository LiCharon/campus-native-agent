"""数据层测试：6 张表（4 业务 + 2 评测）的列/主键/外键/默认值锁定。

M1-T2 退役 tickets/ticket_logs/repairmen/dorms/accounts/announcements/faq，
新增 knowledge_entries/bad_cases（用例随之更新；test_seed 由 M1-T9 重写）。
M3 起"DB 变更走迁移"（alembic）——此处锁定的是 ORM 定义本身，
迁移文件与 ORM 的一致性由 alembic autogenerate 对比（见 test_mysql_smoke）。
"""

from sqlalchemy import inspect

from campus_desk.db.models import BadCase, KnowledgeEntry, User, UserProfile


def _columns(factory, table: str) -> set[str]:
    with factory() as session:
        insp = inspect(session.connection())
        return {c["name"] for c in insp.get_columns(table)}


class TestTables:
    """建表成功 + 关键列存在（跨 SQLite/MySQL 通用断言）。"""

    def test_all_tables_created(self, db_session_factory):
        with db_session_factory() as session:
            insp = inspect(session.connection())
            tables = set(insp.get_table_names())
            expected = {
                "users",
                "user_profiles",
                "knowledge_entries",
                "bad_cases",
                "eval_case",
                "eval_turn",
            }
            assert expected <= tables, f"缺表: {expected - tables}"
            # 退役表必须不在（M1-T2 已删）
            retired = {
                "tickets",
                "ticket_logs",
                "repairmen",
                "dorms",
                "accounts",
                "announcements",
                "faq",
            }
            assert not (retired & tables), f"退役表残留: {retired & tables}"

    def test_knowledge_entries_columns(self, db_session_factory):
        cols = _columns(db_session_factory, "knowledge_entries")
        for name in [
            "id",
            "domain",
            "keywords",
            "question",
            "type",
            "answer",
            "created_at",
        ]:
            assert name in cols, f"knowledge_entries 缺列 {name}"

    def test_bad_cases_columns(self, db_session_factory):
        cols = _columns(db_session_factory, "bad_cases")
        for name in ["id", "user_id", "question", "reply", "status", "created_at"]:
            assert name in cols, f"bad_cases 缺列 {name}"

    def test_eval_tables_columns(self, db_session_factory):
        case_cols = _columns(db_session_factory, "eval_case")
        for name in [
            "id",
            "category",
            "student_input",
            "intent",
            "expected_route",
            "secondary_intents",
            "note",
        ]:
            assert name in case_cols, f"eval_case 缺列 {name}"
        turn_cols = _columns(db_session_factory, "eval_turn")
        for name in ["id", "case_id", "seq", "student_reply", "expect"]:
            assert name in turn_cols, f"eval_turn 缺列 {name}"


class TestConstraints:
    """主键/外键约束锁定（跨库）。"""

    def test_foreign_keys(self, db_session_factory):
        with db_session_factory() as session:
            insp = inspect(session.connection())
            profile_fks = {fk["referred_table"] for fk in insp.get_foreign_keys("user_profiles")}
            assert profile_fks == {"users"}
            turn_fks = {fk["referred_table"] for fk in insp.get_foreign_keys("eval_turn")}
            assert turn_fks == {"eval_case"}
            # 新增表无外键（知识库/反馈均独立）
            assert insp.get_foreign_keys("knowledge_entries") == []
            assert insp.get_foreign_keys("bad_cases") == []

    def test_new_tables_defaults(self, db_session_factory):
        """默认值锁定：type=info / domain='' / bad_cases.status='待处理'。"""

        with db_session_factory() as session:
            with session.begin():
                e = KnowledgeEntry(keywords="测试", question="测试问题", answer="测试答案")
                session.add(e)
                b = BadCase(user_id="student-001", question="测试问题")
                session.add(b)
            session.expire_all()
            got_e = session.get(KnowledgeEntry, e.id)
            assert got_e.type == "info"
            assert got_e.domain == ""
            got_b = session.get(BadCase, b.id)
            assert got_b.status == "PENDING"
            assert got_b.reply == ""


class TestQueries:
    """基础查询路径（工具层依赖面）。"""

    def test_status_index_exists(self, db_session_factory):
        with db_session_factory() as session:
            insp = inspect(session.connection())
            bc_indexes = {i["name"] for i in insp.get_indexes("bad_cases")}
            assert any("status" in ix for ix in bc_indexes), (
                "bad_cases.status 应建索引（工作台按状态过滤）"
            )
            ke_indexes = {i["name"] for i in insp.get_indexes("knowledge_entries")}
            assert any("domain" in ix for ix in ke_indexes), (
                "knowledge_entries.domain 应建索引（按领域筛选）"
            )


class TestRetainedModels:
    """M1-T2 保留模型（User/UserProfile）写入路径锁定。"""

    def test_user_profile_roundtrip(self, db_session_factory):
        with db_session_factory() as session, session.begin():
            u = session.get(User, "student-001")
            assert u is not None and u.role == "student"
            p = UserProfile(user_id=u.id, building="3号楼", frequent_categories="水电")
            session.add(p)
            session.flush()
            assert p.user_id == u.id


def test_empty_room_columns(db_session_factory):
    """M2：empty_rooms 种子行关键字段非空且 weekday 在 1-7。"""
    from campus_desk.db.models import EmptyRoom

    with db_session_factory() as session:
        row = session.query(EmptyRoom).first()
        assert row.building and row.room and row.weekday in range(1, 8) and row.period


def test_knowledge_entry_type_and_bad_case(db_session_factory):
    from campus_desk.db.models import BadCase, KnowledgeEntry

    with db_session_factory() as s, s.begin():
        e = KnowledgeEntry(domain="教务", keywords="校历,放假", question="什么时候放寒假？",
                           type="info", answer="以学校通知为准。")
        s.add(e)
        s.flush()
        b = BadCase(user_id="student-001", question="怎么交学费？", reply="", status="PENDING")
        s.add(b)
        s.flush()
        assert e.id and e.type == "info"
        assert b.status == "PENDING"
