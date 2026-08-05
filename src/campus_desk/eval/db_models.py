"""评测集同步表（M3：JSON 剧本 → DB 入库）。

独立于业务表（eval_case/eval_turn），供：
- scripts/ingest_eval_data.py：JSON→MySQL 入库（需求 §10 已拍板，与业务数据同库不同域）
- eval runner 未来从库读剧本（M5 评测闭环）

expect/secondary_intents 用 JSON 文本存（无查询需求，保持表结构简单）。
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from campus_desk.db.base import Base


class EvalCase(Base):
    __tablename__ = "eval_case"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # repair-001
    category: Mapped[str] = mapped_column(String(16), index=True)
    student_input: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(16))
    expected_route: Mapped[str] = mapped_column(String(16))
    secondary_intents: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    note: Mapped[str] = mapped_column(Text, default="")


class EvalTurn(Base):
    __tablename__ = "eval_turn"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("eval_case.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    student_reply: Mapped[str] = mapped_column(Text)
    expect: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组（tool:/status: 断言）
