"""管理页审查路由（M3，设计 §5.5 管理员审查）：待审列表 + 补入知识库/驳回。

- GET /api/admin/reviews?kind=bad_cases|suggestions&status=PENDING：待审列表
  （每条带 suggested_keywords 预填建议，前端弹窗可编辑）
- POST /api/admin/reviews/{kind}/{id}/adopt {domain,type,keywords,answer}：
  补入 knowledge_entries + 来源状态流转（bad_cases→RESOLVED / suggestions→ADOPTED）
- POST /api/admin/reviews/{kind}/{id}/dismiss：驳回（bad_cases→RESOLVED /
  suggestions→REJECTED，不产生知识条目）

权限：require_roles("admin")（拍板：仅管理员审查；cs_staff 客服不开放）。
已处理记录（非 PENDING）再操作 → 404（幂等防重复补入）。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from campus_desk.api.deps import AuthUser, get_session_factory, require_roles
from campus_desk.api.schemas import (
    AdoptRequest,
    ReviewActionResponse,
    ReviewItem,
    ReviewKind,
    ReviewListResponse,
)
from campus_desk.db.models import BadCase, KnowledgeEntry, Suggestion
from campus_desk.db.session import SessionFactory
from campus_desk.knowledge.suggest import suggest_keywords

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 来源模型 + 动作后的状态流转（adopt/dismiss）
_SOURCE_MODEL = {"bad_cases": BadCase, "suggestions": Suggestion}
_SOURCE_ACTION_STATUS = {
    "bad_cases": {"adopt": "RESOLVED", "dismiss": "RESOLVED"},
    "suggestions": {"adopt": "ADOPTED", "dismiss": "REJECTED"},
}


def _fetch_pending(kind: str, rid: int, session_factory: SessionFactory):
    """取待审行；不存在或已处理 → 404（已处理不可再操作）。"""
    model = _SOURCE_MODEL[kind]
    with session_factory() as session:
        row = session.get(model, rid)
    if row is None or row.status != "PENDING":
        raise HTTPException(status_code=404, detail="待审记录不存在或已处理")
    return row


@router.get("/reviews", response_model=ReviewListResponse)
def list_reviews(
    kind: ReviewKind,
    status: str = "PENDING",
    user: AuthUser = Depends(require_roles("admin")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """待审列表（按时间倒序）；bad_cases 带 reply 字段，suggestions 无。"""
    model = _SOURCE_MODEL[kind]
    with session_factory() as session:
        rows = (
            session.execute(
                select(model)
                .where(model.status == status)
                .order_by(model.created_at.desc())
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
                reply=getattr(row, "reply", ""),
                note=row.note,
                status=row.status,
                created_at=row.created_at,
                suggested_keywords=suggest_keywords(row.question),
            )
            for row in rows
        ]
    )


@router.post("/reviews/{kind}/{rid}/adopt", response_model=ReviewActionResponse)
def adopt_review(
    kind: ReviewKind,
    rid: int,
    payload: AdoptRequest,
    user: AuthUser = Depends(require_roles("admin")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """采纳：补入知识库（question 取来源问题，管理员填 domain/type/keywords/answer）。"""
    row = _fetch_pending(kind, rid, session_factory)
    with session_factory() as session, session.begin():
        session.add(
            KnowledgeEntry(
                domain=payload.domain,
                keywords=payload.keywords,
                question=row.question,
                type=payload.type,
                answer=payload.answer,
            )
        )
        row = session.get(_SOURCE_MODEL[kind], rid)
        row.status = _SOURCE_ACTION_STATUS[kind]["adopt"]
        new_status = row.status
    return ReviewActionResponse(id=rid, status=new_status)


@router.post("/reviews/{kind}/{rid}/dismiss", response_model=ReviewActionResponse)
def dismiss_review(
    kind: ReviewKind,
    rid: int,
    user: AuthUser = Depends(require_roles("admin")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """驳回：不补入知识库，仅流转来源状态（bad_cases→RESOLVED / suggestions→REJECTED）。"""
    row = _fetch_pending(kind, rid, session_factory)
    with session_factory() as session, session.begin():
        row = session.get(_SOURCE_MODEL[kind], rid)
        row.status = _SOURCE_ACTION_STATUS[kind]["dismiss"]
        new_status = row.status
    return ReviewActionResponse(id=rid, status=new_status)
