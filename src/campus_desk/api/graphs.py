"""图单例注册表（M1-ZJUT）：entry 全局共享 + per-user knowledge 图缓存 + 全局锁串行化 turn。

并发约束：SqliteSaver 非线程安全 + 共享 checkpointer.db → turn_lock 串行化；
每用户独立 SqliteSaver 连接实例；uvicorn 必须 --workers 1。
"""

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from campus_desk.db.session import SessionFactory
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn as orchestrator_turn
from campus_desk.knowledge.graph import build_knowledge_graph
from campus_desk.query.graph import build_query_graph

# 相对仓库根的稳定路径（T8 Minor：原 "checkpointer.db" 相对 CWD，换启动目录就漂移）。
# api → campus_desk → src → 仓库根（parents[3]）；与 .gitignore 的 *.db 规则对齐。
CHECKPOINTER_DB = str(Path(__file__).resolve().parents[3] / "checkpointer.db")


@dataclass
class GraphBundle:
    entry: object
    knowledge: object
    query: object


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
        if bundle is None:
            with self._build_lock:
                bundle = self._bundles.get(user_id)
                if bundle is None:
                    bundle = self._build_bundle(user_id)
                    self._bundles[user_id] = bundle
        return bundle

    def _build_bundle(self, user_id: str) -> GraphBundle:
        if self._bundle_factory is not None:
            return self._bundle_factory(user_id)
        knowledge = build_knowledge_graph(
            self._session_factory,
            checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINTER_DB, check_same_thread=False)),
            user_id=user_id,
        )
        query = build_query_graph(
            self._session_factory,
            checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINTER_DB, check_same_thread=False)),
            user_id=user_id,
        )
        return GraphBundle(entry=self._entry, knowledge=knowledge, query=query)


def run_turn(registry: GraphRegistry, user_id: str, thread_id: str, msg: str) -> dict:
    """锁内调 orchestrator.turn（同步；FastAPI 路由用 def 走线程池）。"""
    bundle = registry.bundle_for(user_id)
    with registry.turn_lock:
        return orchestrator_turn(bundle.entry, bundle.knowledge, bundle.query, thread_id, msg, user_id=user_id)
