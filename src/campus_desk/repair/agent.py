"""RepairAgent 组装（M3）：默认依赖 + SqliteSaver checkpointer + 图构建。

生产用：build_repair_agent(session_factory) → 默认 DeepSeek 抽取/分类 + SqliteSaver
（checkpointer.db，会话恢复——M1 已验证 SqliteSaver 中断恢复语义）。
测试用：build_repair_graph(checkpointer=InMemorySaver()) 注入 fake。
"""

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver

from campus_desk.db.session import SessionFactory
from campus_desk.repair.graph import build_repair_graph

CHECKPOINTER_DB = "checkpointer.db"  # 会话库（SQLite 官方 SqliteSaver，零运维）


def build_repair_agent(
    session_factory: SessionFactory,
    *,
    user_id: str = "student-001",
    actor: str = "student-001",
):
    """默认组装：真 LLM 抽取/分类 + 文件级 SqliteSaver。"""
    saver = SqliteSaver(sqlite3.connect(CHECKPOINTER_DB, check_same_thread=False))
    return build_repair_graph(
        session_factory,
        checkpointer=saver,
        user_id=user_id,
        actor=actor,
        default_contact="学生",
    )
