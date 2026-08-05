"""QualityGraph（M4，需求 §6 QualityAgent 回访）：提醒 → 采集满意度 → 写工单评价。

节点链：remind（待回访提醒，纯逻辑）→ wait（唯一 interrupt）→ collect（解析+落库）。

- 触达方式已拍死：学生下次进对话时主动提醒一句（惰性触发，无调度器；
  问卷挂工单详情页是 M6 前端的事）
- 一次提醒一个工单（最老优先由 pending.py 排序），评完下一轮再提醒下一个
- 采集解析：数字 1-5 → rating；其余文本 → review_comment（宽松收，不硬答）
- 评价写入 = 字段更新不是状态跳转（不开事务日志——评价不属于生命周期）
"""

import re
from datetime import UTC, datetime
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt  # resume 由编排层传 Command

from campus_desk.db.models import Ticket
from campus_desk.db.session import SessionFactory


class QualityState(TypedDict):
    user_input: str
    student_answer: str | None
    pending_tickets: list[dict]  # [{ticket_id, description}]（orchestrator 查好注入）
    pending_question: str | None
    reply: str
    outcome: Literal["remind", "collected"] | None
    finished: bool
    _consumed: bool


def _make_remind():
    def remind(state: QualityState) -> dict:
        """提醒轮：组装回访问卷（列最近一个待回访工单）。"""
        ticket = state["pending_tickets"][0]
        question = (
            f"您之前的工单 #{ticket['ticket_id']}『{ticket['description']}』已解决，"
            f"方便给个评价吗？回复 1-5 分（5 最满意），或直接描述您的感受。"
        )
        return {"pending_question": question, "reply": question, "outcome": "remind"}

    return remind


def _make_wait():
    def wait(state: QualityState) -> dict:
        """唯一 interrupt 节点（RepairGraph 同款：按 value 匹配暂停点）。"""
        answer = interrupt(state["pending_question"])
        return {"student_answer": str(answer)}

    return wait


def _parse_review(answer: str) -> tuple[int | None, str | None]:
    """评分解析：数字 1-5 → rating；其余文本 → comment（宽松收，不硬答）。"""
    m = re.search(r"[1-5]", answer)
    rating = int(m.group()) if m else None
    comment = (
        answer.strip() if answer.strip() and not re.fullmatch(r"\s*[1-5]\s*", answer) else None
    )
    return rating, comment


def _make_collect(session_factory: SessionFactory):
    def collect(state: QualityState) -> dict:
        """采集轮：解析评分 → 写 tickets 评价字段 → 感谢结束。"""
        ticket = state["pending_tickets"][0]
        answer = state.get("student_answer") or ""
        rating, comment = _parse_review(answer)
        with session_factory() as session, session.begin():
            row = session.get(Ticket, ticket["ticket_id"])
            if row is not None:
                row.rating = rating
                row.review_comment = comment
                row.reviewed_at = datetime.now(UTC)
        thanks = "谢谢您的评价！" + (
            "我们会继续改进。" if rating is not None and rating < 4 else ""
        )
        return {"reply": thanks, "outcome": "collected", "finished": True}

    return collect


def build_quality_graph(session_factory: SessionFactory, checkpointer=None):
    """构建 QualityGraph。checkpointer 必传（interrupt 需持久化；测试传 InMemorySaver）。"""
    graph = (
        StateGraph(QualityState)
        .add_node("remind", _make_remind())
        .add_node("wait", _make_wait())
        .add_node("collect", _make_collect(session_factory))
        .add_edge(START, "remind")
        .add_edge("remind", "wait")
        .add_edge("wait", "collect")
        .add_edge("collect", END)
    )
    return graph.compile(checkpointer=checkpointer)
