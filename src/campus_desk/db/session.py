"""数据库会话工厂（M3）。

生命周期设计：
- 工具调用/业务函数：每次调用开短会话（`with factory() as s: with s.begin():`），
  无跨调用事务——确定性工具天然幂等，短会话防连接泄漏
- API 请求（M6）：get_db() FastAPI 依赖，请求级会话
- 测试：注入 SQLite 内存库工厂（StaticPool 单连接共享，见 tests/conftest.py）

SQLAlchemy 2.0 风格：sessionmaker 显式事务（session.begin()）。
"""

from collections.abc import Callable, Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from campus_desk.config import settings

SessionFactory = Callable[[], Session]


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    """按 URL 建引擎 + 工厂。pool_pre_ping：连接失效自动重连（MySQL 8 空闲回收）。
    expire_on_commit=False：commit 后对象仍可读（工具层短会话必配）。"""
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def default_session_factory() -> sessionmaker[Session]:
    """业务运行会话工厂（读 settings.database_url，MySQL）。未配置抛错——防静默连错库。"""
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL 未配置（.env）——M3 起业务数据需 MySQL；测试请注入 SQLite 内存库工厂"
        )
    return create_session_factory(settings.database_url)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖（M6 接口层用；M3 预留，业务代码直接传工厂）。"""
    factory = default_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
