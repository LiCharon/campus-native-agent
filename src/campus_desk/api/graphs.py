"""图单例注册表（M1-ZJUT）：entry 全局共享 + per-user knowledge 图缓存 + 全局锁串行化 turn。

并发约束：SqliteSaver 非线程安全 + 共享 checkpointer.db → turn_lock 串行化；
每用户独立 SqliteSaver 连接实例；uvicorn 必须 --workers 1。
"""

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import select

from campus_desk.config import settings
from campus_desk.db.session import SessionFactory
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn as orchestrator_turn
from campus_desk.knowledge.graph import build_knowledge_graph
from campus_desk.query.graph import build_query_graph

# 相对仓库根的稳定路径（T8 Minor：原 "checkpointer.db" 相对 CWD，换启动目录就漂移）。
# api → campus_desk → src → 仓库根（parents[3]）；与 .gitignore 的 *.db 规则对齐。
CHECKPOINTER_DB = str(Path(__file__).resolve().parents[3] / "checkpointer.db")


def _fetch_profile(session_factory, user_id: str) -> tuple[str | None, str | None]:
    """查画像注入文本与 updated_at（图构建期/失效检查用）。

    返回 (profile_text, updated_at_iso)；无画像行/失败 → (None, None)，不阻断。
    M7-ZJUT：profile_text 为空串视为无画像（None），注入方判断有值才拼。
    """
    try:
        from campus_desk.db.models import UserProfile
        from campus_desk.profile.extract import format_profile_text

        with session_factory() as session:
            profile = session.get(UserProfile, user_id)
        if profile is None:
            return None, None
        text = format_profile_text(
            {
                "building": profile.building,
                "frequent_categories": profile.frequent_categories,
            }
        )
        updated = profile.updated_at.isoformat() if profile.updated_at else None
        return (text or None), updated
    except Exception:  # noqa: BLE001 — 画像注入旁路，失败按无画像处理
        return None, None


@dataclass
class GraphBundle:
    entry: object
    knowledge: object
    query: object
    profile_updated_at: str | None = None  # M7-ZJUT：构建时的画像版本（失效比对用）


class GraphRegistry:
    def __init__(self, session_factory: SessionFactory, *, bundle_factory=None):
        self._session_factory = session_factory
        self._entry = build_entry_graph()
        self._bundles: dict[str, GraphBundle] = {}
        self._build_lock = threading.Lock()
        self.turn_lock = threading.Lock()
        self._bundle_factory = bundle_factory

    def bundle_for(self, user_id: str) -> GraphBundle:
        bundle = self._bundles.get(user_id)
        if bundle is not None:
            # M7-ZJUT：画像 updated_at 变化 → 重建 bundle（每轮抽取后"第二问"实时注入，无需重启）。
            # PK 轻量 SELECT（每轮一次，chat 本身已多次 DB 操作），锁外检查只是快速路径。
            _, updated_at = _fetch_profile(self._session_factory, user_id)
            if updated_at != bundle.profile_updated_at:
                bundle = None
        if bundle is None:
            with self._build_lock:
                bundle = self._bundles.get(user_id)
                if bundle is not None:
                    _, updated_at = _fetch_profile(self._session_factory, user_id)
                    if updated_at != bundle.profile_updated_at:
                        self._bundles.pop(user_id, None)
                        bundle = None
                if bundle is None:
                    bundle = self._build_bundle(user_id)
                    self._bundles[user_id] = bundle
        return bundle

    def _build_bundle(self, user_id: str) -> GraphBundle:
        if self._bundle_factory is not None:
            return self._bundle_factory(user_id)
        profile_text, profile_updated_at = _fetch_profile(self._session_factory, user_id)
        knowledge = build_knowledge_graph(
            self._session_factory,
            checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINTER_DB, check_same_thread=False)),
            user_id=user_id,
            profile=profile_text or "",
        )
        query = build_query_graph(
            self._session_factory,
            checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINTER_DB, check_same_thread=False)),
            user_id=user_id,
            profile_text=profile_text or "",
        )
        return GraphBundle(
            entry=self._entry,
            knowledge=knowledge,
            query=query,
            profile_updated_at=profile_updated_at,
        )


def _recent_history(
    thread_id: str,
    session_factory: SessionFactory,
    exclude_message_id: int | None,
    n: int,
) -> list[str]:
    """取会话最近 n 条 user 文本（排除当前消息），升序，供 LLM 上下文窗口。

    chat.py 先落库当前消息再 run_turn，故当前条已在库；用 exclude_message_id
    显式排除，单/多 worker 都正确（为 M12+ 水平并发预留，不依赖执行顺序）。
    失败按空返回（上下文窗口为增强项，不阻断主流程）。
    """
    try:
        from campus_desk.db.models import Conversation, Message

        with session_factory() as session:
            conv_id = session.execute(
                select(Conversation.id).where(Conversation.thread_id == thread_id)
            ).scalar()
            if conv_id is None:
                return []
            stmt = select(Message.content).where(
                Message.conversation_id == conv_id, Message.role == "user"
            )
            if exclude_message_id is not None:
                stmt = stmt.where(Message.id != exclude_message_id)
            stmt = stmt.order_by(Message.id.desc()).limit(n)
            rows = session.execute(stmt).scalars().all()
        return list(reversed(rows))
    except Exception:  # noqa: BLE001 — 上下文窗口失败不阻断主对话
        return []


def run_turn(
    registry: GraphRegistry,
    user_id: str,
    thread_id: str,
    msg: str,
    current_message_id: int | None = None,
) -> dict:
    """锁内调 orchestrator.turn（同步；FastAPI 路由用 def 走线程池）。

    current_message_id：chat.py 落库的当前 user 消息 id，用于从近期历史排除自身
    （避免当前问题重复进上下文窗口）。无值时 _recent_history 不排除任何条。
    """
    bundle = registry.bundle_for(user_id)
    recent = _recent_history(
        thread_id, registry._session_factory, current_message_id, settings.context_window_rounds
    )
    with registry.turn_lock:
        return orchestrator_turn(
            bundle.entry,
            bundle.knowledge,
            bundle.query,
            thread_id,
            msg,
            user_id=user_id,
            recent=recent,
        )
