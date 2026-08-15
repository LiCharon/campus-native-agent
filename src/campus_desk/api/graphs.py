"""图单例注册表（M1 临时最小版）：per-user entry 图缓存 + 全局锁串行化 turn。

ZJUT Native Agent 演进中：Repair/Consult/Quality/Complaint 图已退役（M1-T1），
GraphBundle 只保留 entry（无状态无 checkpointer，全局共享一个）。本文件后续
任务完整重写，当前只保证 import 不炸、chat 路由可跑。

并发约束（沿用原设计）：全局一把 turn_lock 串行化所有 turn；
uvicorn 必须 --workers 1。
"""

import threading
from dataclasses import dataclass

from campus_desk.db.session import SessionFactory
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn as orchestrator_turn


@dataclass
class GraphBundle:
    """一个用户的图集合（M1 临时版：仅 entry，无状态可共享）。"""

    entry: object


class GraphRegistry:
    """per-user 图注册表：懒构建 + 缓存；turn 全局锁串行化。"""

    def __init__(self, session_factory: SessionFactory, *, bundle_factory=None):
        self._session_factory = session_factory
        self._entry = build_entry_graph()  # 无状态无 checkpointer，全局共享一个
        self._bundles: dict[str, GraphBundle] = {}
        self._build_lock = threading.Lock()
        # 全局锁（跨用户）：下游图（恢复后）的 checkpointer 非线程安全
        self.turn_lock = threading.Lock()
        # 测试注入点：bundle_factory(user_id) -> GraphBundle（Fake LLM 图）
        self._bundle_factory = bundle_factory

    def bundle_for(self, user_id: str) -> GraphBundle:
        """懒构建用户图集合（M1 临时版：仅 entry）。"""
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
        return GraphBundle(entry=self._entry)


def run_turn(registry: GraphRegistry, user_id: str, thread_id: str, msg: str) -> dict:
    """锁内调 orchestrator.turn（M1 临时版：仅 entry 分流，返回 route/reply）。"""
    bundle = registry.bundle_for(user_id)
    with registry.turn_lock:
        return orchestrator_turn(
            bundle.entry, None, None, thread_id, msg, user_id=user_id
        )
