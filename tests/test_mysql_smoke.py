"""MySQL 集成冒烟（M3，需外部环境 → 不进 CI）。

跳过条件：.env 未配 DATABASE_URL。本地开发跑真 MySQL：
迁移已应用（alembic upgrade head）+ 种子已入库（scripts/seed_db.py）前提下，
验证建单→状态流转往返，确认 SQLAlchemy + pymysql + MySQL8 链路无方言问题。
"""

import pytest

from campus_desk.config import settings
from campus_desk.db.session import create_session_factory

pytestmark = pytest.mark.skipif(
    not settings.database_url, reason="DATABASE_URL 未配置——需外部 MySQL，不进 CI"
)


@pytest.fixture(scope="module")
def mysql_factory():
    return create_session_factory(settings.database_url)


def test_mysql_connect_and_seed(mysql_factory):
    """连接 + 种子已入库（表结构与 ORM 一致性的最小验证）。"""
    from campus_desk.db.models import Faq, Repairman, User

    with mysql_factory() as session:
        assert session.query(User).count() == 9
        assert session.query(Repairman).count() == 8
        assert session.query(Faq).count() == 24


def test_mysql_ticket_roundtrip(mysql_factory):
    """MySQL 方言往返：建单（中文）+ 更新字段 + 查询。

    注意 MySQL 下 SELECT 会隐式开启事务——查询必须放进 begin() 块内，
    与 SQLite 行为不同（SQLite SELECT 不隐式开事务）。
    """
    from campus_desk.db.models import Ticket

    with mysql_factory() as session:
        with session.begin():
            t = Ticket(user_id="student-001", description="3号楼502灯管闪烁", contact="李华")
            session.add(t)
            session.flush()  # 拿自增 id（仍在同一事务内）
        with session.begin():
            got = session.get(Ticket, t.id)
            assert got.description == "3号楼502灯管闪烁"
            assert got.status == "SUBMITTED"
            got.status = "CLOSED"
        with session.begin():
            assert session.get(Ticket, t.id).status == "CLOSED"


def test_mysql_orm_metadata_matches_schema(mysql_factory):
    """ORM metadata 与 MySQL 实表对比：列名齐全（迁移文件与 ORM 一致性烟雾）。"""
    from sqlalchemy import inspect

    from campus_desk.db.models import Ticket

    with mysql_factory() as session:
        insp = inspect(session.connection())
        db_cols = {c["name"] for c in insp.get_columns("tickets")}
        orm_cols = {c.key for c in Ticket.__mapper__.column_attrs}
    assert db_cols >= orm_cols, f"ORM 定义超出实表: {orm_cols - db_cols}"
