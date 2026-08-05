"""待回访查询（M4 QualityAgent，需求 §6）：工单关闭 24h 后回访采集满意度。

触发方式已拍板 = 惰性触发（不装 APScheduler）：学生下次进对话时查询——
CLOSED + 未回访（reviewed_at IS NULL）+ 关闭超 24h（closed_at < now-24h）。
超时升级的定时扫描（P1 4h / P2 48h）留 M5 与评测闭环一起做。
"""

from datetime import UTC, datetime, timedelta

from campus_desk.db.models import Ticket
from campus_desk.db.session import SessionFactory

REVIEW_DELAY = timedelta(hours=24)  # 关闭 24h 后可回访（需求 §6）


def find_pending_reviews(session_factory: SessionFactory, user_id: str) -> list[dict]:
    """查待回访工单（按关闭时间倒序，最近优先）。返回 [{ticket_id, description}]。"""
    cutoff = datetime.now(UTC) - REVIEW_DELAY
    with session_factory() as session, session.begin():
        rows = (
            session.query(Ticket)
            .filter(
                Ticket.user_id == user_id,
                Ticket.status == "CLOSED",
                Ticket.reviewed_at.is_(None),
                Ticket.closed_at < cutoff,
            )
            .order_by(Ticket.closed_at.desc())
            .limit(3)
            .all()
        )
    return [{"ticket_id": t.id, "description": (t.description or "")[:40]} for t in rows]
