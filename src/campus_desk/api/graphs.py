"""图单例注册表（M6）：per-user 图缓存 + 全局锁串行化 turn。

为什么 per-user 而非全局单例：RepairGraph 的 user_id（建单提交人）是
构建时闭包参数（tools/repair_tools.py create_repair_tools(user_id=...)）——
多用户共用一个图会把所有工单建到同一人名下（M6 实测设计缺陷，注册表修复）。

并发约束（LangGraph SqliteSaver 非线程安全 + 共享 checkpointer.db 文件）：
- 全局一把 turn_lock 串行化所有 turn（并发被串行化，演示场景可接受）
- 每用户各自独立 SqliteSaver 连接实例（repair/consult/complaint 同 thread_id
  键空间必须分实例隔离——test_orchestrator 锁定该语义）
- uvicorn 必须 --workers 1（多 worker = 多注册表 + 多文件连接）
"""

import sqlite3
import threading
from dataclasses import dataclass

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import select

from campus_desk.consult.graph import build_consult_graph
from campus_desk.db.models import User
from campus_desk.db.session import SessionFactory
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn as orchestrator_turn
from campus_desk.quality.graph import build_quality_graph
from campus_desk.repair.agent import CHECKPOINTER_DB
from campus_desk.repair.graph import build_repair_graph


@dataclass
class GraphBundle:
    """一个用户的五图集合（entry 无状态可共享，其余 per-user）。"""

    entry: object
    repair: object
    consult: object
    quality: object
    complaint: object


class GraphRegistry:
    """per-user 图注册表：懒构建 + 缓存；turn 全局锁串行化。"""

    def __init__(self, session_factory: SessionFactory, *, bundle_factory=None):
        self._session_factory = session_factory
        self._entry = build_entry_graph()  # 无状态无 checkpointer，全局共享一个
        self._bundles: dict[str, GraphBundle] = {}
        self._build_lock = threading.Lock()
        # 全局锁（跨用户）：SqliteSaver 非线程安全 + 共享 checkpointer.db 文件
        self.turn_lock = threading.Lock()
        # 测试注入点：bundle_factory(user_id) -> GraphBundle（Fake LLM 图）
        self._bundle_factory = bundle_factory

    def bundle_for(self, user_id: str) -> GraphBundle:
        """懒构建用户图集合（含该用户的 repair/consult/complaint 建单归属）。"""
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
        # 学号注入 consult（query_account_status mock 按学号查 accounts 表）
        with self._session_factory() as session:
            user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        student_no = user.student_no if user else None
        repair = build_repair_graph(
            self._session_factory,
            checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINTER_DB, check_same_thread=False)),
            user_id=user_id,
            actor=user_id,
        )
        complaint = build_repair_graph(
            self._session_factory,
            checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINTER_DB, check_same_thread=False)),
            user_id=user_id,
            actor=user_id,
            ticket_type="complaint",
        )
        consult = build_consult_graph(
            self._session_factory,
            checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINTER_DB, check_same_thread=False)),
            student_no=student_no,
        )
        quality = build_quality_graph(
            self._session_factory,
            checkpointer=SqliteSaver(sqlite3.connect(CHECKPOINTER_DB, check_same_thread=False)),
        )
        return GraphBundle(
            entry=self._entry,
            repair=repair,
            consult=consult,
            quality=quality,
            complaint=complaint,
        )


def run_turn(registry: GraphRegistry, user_id: str, thread_id: str, msg: str) -> dict:
    """锁内调 orchestrator.turn（同步；FastAPI 路由用 def 走线程池）。"""
    bundle = registry.bundle_for(user_id)
    with registry.turn_lock:
        return orchestrator_turn(
            bundle.entry,
            bundle.repair,
            bundle.consult,
            thread_id,
            msg,
            quality_graph=bundle.quality,
            user_id=user_id,
            session_factory=registry._session_factory,
            complaint_graph=bundle.complaint,
        )
