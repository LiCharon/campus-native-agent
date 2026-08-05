"""alembic 环境（M3）。

URL 从 campus_desk.config.settings.database_url 注入（.env 是唯一事实源，
alembic.ini 不硬编码连接串）；target_metadata 含业务表 + 评测表两个 metadata。
prepend_sys_path = src 在 alembic.ini（src layout 导入路径）。
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from campus_desk.config import settings
from campus_desk.db import models  # noqa: F401 — 注册 8 张业务表进 Base.metadata
from campus_desk.db.base import Base
from campus_desk.eval import db_models  # noqa: F401 — 注册 eval 表进 Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = [Base.metadata]


def _get_url() -> str:
    url = settings.database_url
    if not url:
        raise RuntimeError("DATABASE_URL 未配置（.env）——alembic 迁移需要真实数据库 URL")
    return url


def run_migrations_offline() -> None:
    """Offline 模式：仅生成 SQL 不连库（--sql）。"""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online 模式：连接真实数据库执行迁移。"""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _get_url()  # 覆盖 alembic.ini 占位值
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
