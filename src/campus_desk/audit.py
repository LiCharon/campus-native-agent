"""审计旁路（M4）：关键操作留痕，失败不阻断主业务（对抗性审查 #2）。

独立事务写入：调用方业务 commit 后调用；内部 try/except 吞异常——
audit_logs 异常/超长等只影响留痕，绝不影响主流程。
detail 截断 256（列长硬限制）。
"""

from __future__ import annotations

from campus_desk.db.models import AuditLog
from campus_desk.db.session import SessionFactory

_AUDIT_DETAIL_MAX = 256


def write_audit(
    session_factory: SessionFactory,
    *,
    user_id: str,
    action: str,
    object_type: str,
    object_id: str = "",
    detail: str = "",
) -> None:
    """写一条审计日志（独立事务 + try/except 旁路）。"""
    try:
        with session_factory() as session, session.begin():
            session.add(
                AuditLog(
                    user_id=user_id,
                    action=action,
                    object_type=object_type,
                    object_id=str(object_id)[:32],
                    detail=detail[:_AUDIT_DETAIL_MAX],
                )
            )
    except Exception:  # noqa: S110, BLE001 - 审计是旁路：写入失败仅吞掉，不影响调用方（对抗性审查 #2）
        pass
