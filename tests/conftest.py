"""测试共享设施：可控 fake LLM stub + SQLite 内存库会话工厂。

IntentClassifier 对 LLM 的依赖面只有一处：`llm.invoke(messages)` 返回带
`.content` 的对象（真 LLM 为 AIMessage）。fake 只对齐这个面：
- 序列元素为 str：作为 invoke 返回的 content（模拟模型输出）
- 序列元素为 Exception：invoke 时抛出（模拟 LLM 网络/服务异常）
- 序列用尽：返回"永远解析失败"的内容

db_session_factory：SQLite 内存库（业务+eval 全表 create_all + 幂等种子）。
⚠️ 内存库必须 StaticPool（所有连接共享同一 DBAPI 连接）+ check_same_thread=False；
单连接不支持并发 → 测试必须串行（不装 pytest-xdist，默认即可）。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from campus_desk.db.base import Base
from campus_desk.db.seed import seed_all
from campus_desk.eval import db_models  # noqa: F401 — 注册 eval 表进 Base.metadata


class FakeStructuredLLM:
    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if not self.sequence:
            return type("FakeAIMessage", (), {"content": "这不是JSON"})()
        item = self.sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return type("FakeAIMessage", (), {"content": item})()


class FakeIntentClassifier:
    """固定返回预设 IntentResult 的 stub，供图测试注入（不依赖 LLM）。"""

    def __init__(self, result):
        self.result = result

    def classify(self, user_input):
        return self.result


@pytest.fixture
def db_session_factory():
    """SQLite 内存库会话工厂（全表 + 种子）。每个测试独立库，互不污染。

    注意：StaticPool 单连接 + 内存库 = 同一连接跨会话共享，
    fixture 内完成 create_all 与种子后，业务代码可正常多会话读写。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_all(factory)
    yield factory
    engine.dispose()
