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


class FakeIntentClassifier:
    """固定返回预设 IntentResult 的 stub，供图测试注入（不依赖 LLM）。"""

    def __init__(self, result):
        self.result = result

    def classify(self, user_input):
        return self.result


class FakeFieldExtractor:
    """字段抽取器 stub：序列消费（每轮抽取弹出一个），用尽返回 default。

    extract(text) 与真实抽取器同签名；description 取输入原文（merge 语义对齐）。
    """

    def __init__(self, sequence=None, default=None):
        self.sequence = list(sequence or [])
        self.default = default

    def extract(self, text):
        if self.sequence:
            item = self.sequence.pop(0)
            if not item.description:  # 序列项可只给抽取字段，description 用输入
                item = item.model_copy(update={"description": text})
            return item
        if self.default is not None:
            return self.default.model_copy(update={"description": text})
        from campus_desk.repair.drafting import rule_extract

        return rule_extract(text)


class FakeRepairClassifier:
    """分类定级 stub：序列消费（每轮 classify 弹出一个），用尽返回 default。

    calls 记录每次 (description, profile_context)——M4 画像注入断言用。
    """

    def __init__(self, sequence=None, default=None):
        self.sequence = list(sequence or [])
        self.default = default
        self.calls: list[tuple[str, str | None]] = []

    def classify(self, description, profile_context=None):
        self.calls.append((description, profile_context))
        if self.sequence:
            return self.sequence.pop(0)
        return self.default


class FakeConsultDecider:
    """咨询决策 stub（M4）：序列消费（每轮 decide 弹出一个），用尽返回 default。

    calls 记录每次 (history, user_text, tool_results)——工具调用/追问断言用。
    """

    def __init__(self, sequence=None, default=None):
        self.sequence = list(sequence or [])
        self.default = default
        self.calls: list[tuple[list, str, list | None]] = []

    def decide(self, history, user_text, tool_results=None, student_no=None):
        self.calls.append((list(history), user_text, tool_results))
        if self.sequence:
            return self.sequence.pop(0)
        return self.default


@pytest.fixture
def api_client(db_session_factory):
    """M6 API 测试客户端：全 Fake LLM 图 + SQLite 内存库 + TestClient。

    绝不无参 create_app()（会建真 LLM + 写 checkpointer.db）；测试只走注入版。
    """
    from fastapi.testclient import TestClient
    from langgraph.checkpoint.memory import InMemorySaver

    from campus_desk.api.app import create_app
    from campus_desk.api.graphs import GraphBundle, GraphRegistry
    from campus_desk.consult.decide import ConsultDecision
    from campus_desk.consult.graph import build_consult_graph
    from campus_desk.entry.entry_graph import build_entry_graph
    from campus_desk.entry.intent import IntentResult
    from campus_desk.quality.graph import build_quality_graph
    from campus_desk.repair.classify import ClassificationResult
    from campus_desk.repair.drafting import DraftExtract
    from campus_desk.repair.graph import build_repair_graph

    def _bundle(user_id: str) -> GraphBundle:
        entry = build_entry_graph(
            classifier=FakeIntentClassifier(
                IntentResult(intent="repair", confidence=0.9, secondary_intents=[], reason="测试")
            )
        )
        repair = build_repair_graph(
            db_session_factory,
            extractor=FakeFieldExtractor(
                default=DraftExtract(description="", building="3号楼", room="502", contact="李华")
            ),
            classifier=FakeRepairClassifier(
                default=ClassificationResult(category="水电", priority="P2", confidence=0.9)
            ),
            checkpointer=InMemorySaver(),
            user_id=user_id,
            actor=user_id,
        )
        complaint = build_repair_graph(
            db_session_factory,
            extractor=FakeFieldExtractor(
                default=DraftExtract(description="", building=None, room=None, contact="李华")
            ),
            checkpointer=InMemorySaver(),
            user_id=user_id,
            actor=user_id,
            ticket_type="complaint",
        )
        consult = build_consult_graph(
            db_session_factory,
            decider=FakeConsultDecider(
                default=ConsultDecision(
                    action="answer", reply="教务密码可在教务系统点忘记密码重置。"
                )
            ),
            checkpointer=InMemorySaver(),
            student_no="2024001",
        )
        quality = build_quality_graph(db_session_factory, checkpointer=InMemorySaver())
        return GraphBundle(
            entry=entry, repair=repair, consult=consult, quality=quality, complaint=complaint
        )

    registry = GraphRegistry(db_session_factory, bundle_factory=_bundle)
    app = create_app(session_factory=db_session_factory, registry=registry)
    return TestClient(app)


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
