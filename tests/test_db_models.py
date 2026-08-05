"""数据层测试：10 张表（8 业务 + 2 评测）的列/主键/外键/唯一约束/默认值锁定。

M3 起"DB 变更走迁移"（alembic）——此处锁定的是 ORM 定义本身，
迁移文件与 ORM 的一致性由 alembic autogenerate 对比（见 test_mysql_smoke）。
"""

from sqlalchemy import inspect

from campus_desk.db.models import Ticket


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
                "tickets",
                "ticket_logs",
                "repairmen",
                "dorms",
                "accounts",
                "announcements",
                "faq",
                "eval_case",
                "eval_turn",
            }
            assert expected <= tables, f"缺表: {expected - tables}"

    def test_tickets_columns(self, db_session_factory):
        cols = _columns(db_session_factory, "tickets")
        for name in [
            "id",
            "user_id",
            "ticket_type",
            "description",
            "contact",
            "category",
            "priority",
            "status",
            "building",
            "location",
            "dept",
            "repairman_id",
            "escalation_count",
            "escalated_at",
            "created_at",
            "updated_at",
        ]:
            assert name in cols, f"tickets 缺列 {name}"

    def test_ticket_logs_audit_columns(self, db_session_factory):
        cols = _columns(db_session_factory, "ticket_logs")
        for name in ["ticket_id", "from_status", "to_status", "actor", "note", "created_at"]:
            assert name in cols, f"ticket_logs 缺列 {name}"

    def test_repairmen_columns(self, db_session_factory):
        cols = _columns(db_session_factory, "repairmen")
        for name in ["id", "name", "dept", "trade", "phone", "on_duty"]:
            assert name in cols, f"repairmen 缺列 {name}"

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
    """主键/外键/唯一约束锁定（跨库）。"""

    def test_foreign_keys(self, db_session_factory):
        with db_session_factory() as session:
            insp = inspect(session.connection())
            ticket_fks = {fk["referred_table"] for fk in insp.get_foreign_keys("tickets")}
            assert {"users", "repairmen"} <= ticket_fks
            log_fks = {fk["referred_table"] for fk in insp.get_foreign_keys("ticket_logs")}
            assert log_fks == {"tickets"}
            turn_fks = {fk["referred_table"] for fk in insp.get_foreign_keys("eval_turn")}
            assert turn_fks == {"eval_case"}

    def test_uniques(self, db_session_factory):
        with db_session_factory() as session:
            insp = inspect(session.connection())
            dorm_uniq = {c["column_names"][0] for c in insp.get_unique_constraints("dorms")}
            assert "building" in dorm_uniq
            acct_uniq = {c["column_names"][0] for c in insp.get_unique_constraints("accounts")}
            assert "student_no" in acct_uniq

    def test_escalation_fields_defaults(self, db_session_factory):
        """升级=字段不是状态：默认值锁定（escalation_count=0, escalated_at=None）。"""

        with db_session_factory() as session:
            with session.begin():
                ticket = Ticket(user_id="student-001", description="灯坏了", contact="李华")
                session.add(ticket)
            session.expire_all()
            got = session.get(Ticket, ticket.id)
            assert got.escalation_count == 0
            assert got.escalated_at is None
            assert got.status == "SUBMITTED"
            assert got.priority == "P2"
            assert got.ticket_type == "repair"


class TestQueries:
    """基础查询路径（工具层依赖面）。"""

    def test_status_index_exists(self, db_session_factory):
        with db_session_factory() as session:
            insp = inspect(session.connection())
            indexes = {i["name"] for i in insp.get_indexes("tickets")}
            assert any("status" in ix for ix in indexes), (
                "tickets.status 应建索引（管理列表按状态过滤）"
            )
