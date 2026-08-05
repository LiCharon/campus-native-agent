"""SQLAlchemy 声明式基类 + 统一命名约定（M3 数据层）。

naming_convention 给所有约束统一命名（fk_/uq_/ck_/pk_/ix_ 前缀）：
alembic 迁移可复制性的关键——autogenerate 依赖稳定命名的约束名
比较新旧 schema，未命名约束会让每次 autogenerate 产生假 diff。
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
