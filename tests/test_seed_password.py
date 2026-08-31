"""M15B-② SEED_PASSWORD：演示账号密码可由环境变量覆盖（默认 123456）。

设计约束：
- `_USERS` 元组内的密码仅作占位，seed_all 落库时统一读 `_seed_password()`；
- 幂等坑：seed_all 只回填 `password_hash is None` 的存量用户，
  已 seed 的库改 SEED_PASSWORD 重跑**不会**更新旧账号（用 M6 重置密码改）。
"""

import os

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from campus_desk.db.models import Base, User
from campus_desk.db.seed import _seed_password, seed_all
from campus_desk.security import verify_password


def _build_seeded_factory():
    """独立内存库 + 种子（不依赖 conftest fixture，保证 SEED_PASSWORD 生效）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_all(factory)
    return factory


def test_seed_password_defaults_to_123456():
    """未设置 SEED_PASSWORD → 默认 123456（clone 开箱即用）。"""
    if "SEED_PASSWORD" in os.environ:
        pytest.skip("本机环境设置了 SEED_PASSWORD，跳过默认值断言")
    assert _seed_password() == "123456"


def test_seed_password_reads_env(monkeypatch):
    """设置 SEED_PASSWORD → 优先使用环境变量值。"""
    monkeypatch.setenv("SEED_PASSWORD", "Str0ng!Pass2026")
    assert _seed_password() == "Str0ng!Pass2026"


def test_seed_all_uses_env_password(monkeypatch):
    """种子账号落库密码 = SEED_PASSWORD（新密码能登、默认 123456 不能）。"""
    monkeypatch.setenv("SEED_PASSWORD", "Str0ng!Pass2026")
    factory = _build_seeded_factory()
    with factory() as session:
        user = session.execute(
            select(User).where(User.id == "student-001")
        ).scalar_one()
        assert verify_password("Str0ng!Pass2026", user.password_hash)
        assert not verify_password("123456", user.password_hash)


def test_seed_all_default_password_still_works():
    """未设置 SEED_PASSWORD → 种子账号仍为 123456（回归护栏）。"""
    if "SEED_PASSWORD" in os.environ:
        pytest.skip("本机环境设置了 SEED_PASSWORD，跳过默认值断言")
    factory = _build_seeded_factory()
    with factory() as session:
        user = session.execute(
            select(User).where(User.id == "student-001")
        ).scalar_one()
        assert verify_password("123456", user.password_hash)
