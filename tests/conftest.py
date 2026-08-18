"""测试共享设施：可控 fake LLM stub + SQLite 内存库会话工厂 + api_client。

IntentClassifier 对 LLM 的依赖面只有一处：`llm.invoke(messages)` 返回带
`.content` 的对象（真 LLM 为 AIMessage）。fake 只对齐这个面：
- 序列元素为 str：作为 invoke 返回的 content（模拟模型输出）
- 序列元素为 Exception：invoke 时抛出（模拟 LLM 网络/服务异常）
- 序列用尽：返回"永远解析失败"的内容

db_session_factory：SQLite 内存库（业务+eval 全表 create_all + 幂等种子）。
⚠️ 内存库必须 StaticPool（所有连接共享同一 DBAPI 连接）+ check_same_thread=False；
单连接不支持并发 → 测试必须串行（不装 pytest-xdist，默认即可）。

api_client（M1-T8 重建）：TestClient + Fake 图 bundle——FakeClassifier 恒返
knowledge 意图（门控放行）+ InMemorySaver 真 KnowledgeGraph（走真检索），
覆盖 /api/auth/login 与 /api/chat 全链路；bundle_factory 注入避免碰
checkpointer.db 与真 LLM。
"""

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from campus_desk.config import settings
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


class FakeToolLLM:
    """QueryGraph 测试 stub：bind_tools 返回自身；invoke 依序列返回消息。

    - 序列元素为 AIMessage：直接返回（模拟 FC 返回 tool_calls）
    - 序列元素为 Exception：invoke 抛出
    - 序列用尽：返回无 tool_calls 的空消息
    """

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def bind_tools(self, schemas):
        return self

    def invoke(self, messages):
        self.calls += 1
        if not self.sequence:
            return AIMessage(content="", tool_calls=[])
        item = self.sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


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


@pytest.fixture(autouse=True)
def _disable_langfuse(monkeypatch):
    """pytest 双保险：清空 LANGFUSE 双 key + 重置 telemetry 模块缓存。

    保证测试/评测绝不外发 trace（即使开发机 .env 配了 key 也被清空）；
    enabled() 依赖 settings 实时读取，清 key 后全链路走 no-op 路径。
    """
    from campus_desk import telemetry

    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")
    monkeypatch.setattr(telemetry, "_client", None)
    monkeypatch.setattr(telemetry, "_handler", None)


@pytest.fixture(autouse=True)
def _openai_key_fallback(monkeypatch):
    """CI/无 .env 环境兜底：注入假 OPENAI_API_KEY 保证 ChatOpenAI 可构造。

    llm.py 已改为"settings 有 key 才显式传"（M7-CI 修复），无 key 时 SDK 读
    OPENAI_API_KEY 环境变量。测试均走 Fake LLM 图/规则模式，构造后不真调；
    真调路径（env_check/eval 真 LLM 段）按 settings.deepseek_api_key 判空 skip——
    settings 未被污染，skip 语义不变。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")


@pytest.fixture
def api_client(db_session_factory):
    """FastAPI TestClient：Fake 意图（恒 knowledge）+ 真检索 KnowledgeGraph。

    - FakeClassifier 恒返 knowledge（confidence 0.9 过门控）→ chat 测试走真检索
    - KnowledgeGraph 用 InMemorySaver（不落 checkpointer.db）
    - bundle_factory 注入：GraphRegistry 默认生产分支（SqliteSaver）不触发
    - auth 测试与 FakeClassifier 无关（登录只查 users 种子，seed_all 已种
      student-001 / 123456）
    """
    from fastapi.testclient import TestClient
    from langgraph.checkpoint.memory import InMemorySaver

    from campus_desk.api.app import create_app
    from campus_desk.api.graphs import GraphBundle, GraphRegistry
    from campus_desk.entry.entry_graph import build_entry_graph
    from campus_desk.entry.intent import IntentResult
    from campus_desk.knowledge.graph import build_knowledge_graph

    class FakeClassifier:
        def classify(self, user_input):
            return IntentResult(
                intent="knowledge", confidence=0.9, secondary_intents=[], reason="t"
            )

    def _bundle(user_id: str) -> GraphBundle:
        from campus_desk.query.graph import build_query_graph

        entry = build_entry_graph(classifier=FakeClassifier())
        knowledge = build_knowledge_graph(
            db_session_factory, checkpointer=InMemorySaver(), user_id=user_id
        )
        query = build_query_graph(
            db_session_factory, checkpointer=InMemorySaver(), llm=FakeToolLLM([]), user_id=user_id
        )
        return GraphBundle(entry=entry, knowledge=knowledge, query=query)

    registry = GraphRegistry(db_session_factory, bundle_factory=_bundle)
    app = create_app(session_factory=db_session_factory, registry=registry)
    return TestClient(app)
