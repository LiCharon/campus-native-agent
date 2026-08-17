"""客服工作台路由（M4）：待接待队列 + 标记已处理。

- GET /api/cs/queue：bad_cases PENDING 队列（cs_staff + admin 只读）
- POST /api/cs/{id}/resolve：标记已处理（**仅 cs_staff**——接待与知识审查职责分离，
  设计 v3 §2；admin 走 /api/admin/reviews 审查路径）
- 操作写审计日志（旁路）
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from campus_desk.api.deps import AuthUser, get_session_factory, require_perm, require_roles
from campus_desk.api.schemas import (
    ReviewActionResponse,
    ReviewItem,
    ReviewListResponse,
)
from campus_desk.audit import write_audit
from campus_desk.db.models import BadCase
from campus_desk.db.session import SessionFactory
from campus_desk.knowledge.suggest import suggest_keywords

router = APIRouter(prefix="/api/cs", tags=["cs"])


@router.get("/queue", response_model=ReviewListResponse)
def cs_queue(
    user: AuthUser = Depends(require_perm("cs_workbench")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """待接待队列（bad_cases PENDING，按时间倒序）；reply 为空 = 转人工自动沉淀。"""
    with session_factory() as session:
        rows = (
            session.execute(
                select(BadCase)
                .where(BadCase.status == "PENDING")
                .order_by(BadCase.created_at.desc())
            )
            .scalars()
            .all()
        )
    return ReviewListResponse(
        items=[
            ReviewItem(
                id=row.id,
                user_id=row.user_id,
                question=row.question,
                reply=row.reply,
                note=row.note,
                status=row.status,
                created_at=row.created_at,
                suggested_keywords=suggest_keywords(row.question),
            )
            for row in rows
        ]
    )


@router.post("/{rid}/resolve", response_model=ReviewActionResponse)
def cs_resolve(
    rid: int,
    user: AuthUser = Depends(require_roles("cs_staff")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """标记已处理（PENDING→RESOLVED）；不存在/已处理 → 404（幂等防重复）。"""
    with session_factory() as session, session.begin():
        row = session.get(BadCase, rid)
        if row is None or row.status != "PENDING":
            raise HTTPException(status_code=404, detail="待接待记录不存在或已处理")
        row.status = "RESOLVED"
        question = row.question
    write_audit(
        session_factory,
        user_id=user.id,
        action="cs_resolve",
        object_type="bad_case",
        object_id=rid,
        detail=question[:80],
    )
    return ReviewActionResponse(id=rid, status="RESOLVED")
