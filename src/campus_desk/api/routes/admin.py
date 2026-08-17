"""管理路由（M3 审查 + M4 管理特权）：审查补入/驳回 + 知识浏览 + 数据看板 + 用户管理 + 日志。

权限（M4 权限位）：
- reviews（审查补入/驳回）：require_perm("kb_review")——支持被授 kb_review 的 cs_staff
- knowledge（浏览）/ stats / users / logs：对应 view_stats/user_mgmt/view_logs 位

保护（对抗性审查 #3/#4）：
- admin 角色用户不可被禁用/降权（防止锁死系统）
- student 不允许携带附加权限位（授予对象限 cs_staff/admin）

审计：adopt/dismiss/users 操作写 audit_logs（旁路）。
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from campus_desk.api.deps import AuthUser, get_session_factory, require_perm
from campus_desk.api.schemas import (
    AdoptRequest,
    KnowledgeItem,
    KnowledgeListResponse,
    LogItem,
    LogListResponse,
    ResetPasswordRequest,
    ReviewActionResponse,
    ReviewItem,
    ReviewKind,
    ReviewListResponse,
    StatsResponse,
    UserCreateRequest,
    UserListItem,
    UserListResponse,
    UserUpdateRequest,
)
from campus_desk.audit import write_audit
from campus_desk.db.models import AuditLog, BadCase, KnowledgeEntry, Suggestion, User
from campus_desk.db.session import SessionFactory
from campus_desk.knowledge.suggest import suggest_keywords
from campus_desk.perms import GRANTABLE_PERMS
from campus_desk.security import hash_password

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


def _validate_permissions(role: str, permissions: list[str]) -> None:
    """附加权限位校验：仅白名单位；student 不允许携带（对抗性审查 #4）。"""
    unknown = [p for p in permissions if p not in GRANTABLE_PERMS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"非法的权限位: {', '.join(unknown)}")
    if role == "student" and permissions:
        raise HTTPException(status_code=422, detail="student 角色不允许携带附加权限位")


# ---------- M3 审查（kb_review） ----------


@router.get("/reviews", response_model=ReviewListResponse)
def list_reviews(
    kind: ReviewKind,
    status: str = "PENDING",
    user: AuthUser = Depends(require_perm("kb_review")),
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
    user: AuthUser = Depends(require_perm("kb_review")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """采纳：补入知识库（question 取来源问题，管理员填 domain/type/keywords/answer）。"""
    row = _fetch_pending(kind, rid, session_factory)
    question = row.question
    with session_factory() as session, session.begin():
        session.add(
            KnowledgeEntry(
                domain=payload.domain,
                keywords=payload.keywords,
                question=question,
                type=payload.type,
                answer=payload.answer,
            )
        )
        row = session.get(_SOURCE_MODEL[kind], rid)
        row.status = _SOURCE_ACTION_STATUS[kind]["adopt"]
        new_status = row.status
    write_audit(
        session_factory,
        user_id=user.id,
        action="adopt",
        object_type=kind,
        object_id=rid,
        detail=f"补入知识库: {question[:60]}",
    )
    return ReviewActionResponse(id=rid, status=new_status)


@router.post("/reviews/{kind}/{rid}/dismiss", response_model=ReviewActionResponse)
def dismiss_review(
    kind: ReviewKind,
    rid: int,
    user: AuthUser = Depends(require_perm("kb_review")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """驳回：不补入知识库，仅流转来源状态（bad_cases→RESOLVED / suggestions→REJECTED）。"""
    row = _fetch_pending(kind, rid, session_factory)
    question = row.question
    with session_factory() as session, session.begin():
        row = session.get(_SOURCE_MODEL[kind], rid)
        row.status = _SOURCE_ACTION_STATUS[kind]["dismiss"]
        new_status = row.status
    write_audit(
        session_factory,
        user_id=user.id,
        action="dismiss",
        object_type=kind,
        object_id=rid,
        detail=f"驳回: {question[:60]}",
    )
    return ReviewActionResponse(id=rid, status=new_status)


# ---------- M4 知识库浏览（kb_review） ----------


@router.get("/knowledge", response_model=KnowledgeListResponse)
def list_knowledge(
    domain: str = "",
    type: str = "",
    q: str = "",
    user: AuthUser = Depends(require_perm("kb_review")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """知识条目列表 + 领域/类型/关键词筛选（只读浏览）。"""
    stmt = select(KnowledgeEntry).order_by(KnowledgeEntry.id)
    if domain:
        stmt = stmt.where(KnowledgeEntry.domain == domain)
    if type:
        stmt = stmt.where(KnowledgeEntry.type == type)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (KnowledgeEntry.question.like(like)) | (KnowledgeEntry.keywords.like(like))
        )
    with session_factory() as session:
        rows = session.execute(stmt).scalars().all()
    return KnowledgeListResponse(
        items=[
            KnowledgeItem(
                id=r.id,
                domain=r.domain,
                keywords=r.keywords,
                question=r.question,
                type=r.type,
                answer=r.answer,
            )
            for r in rows
        ]
    )


# ---------- M4 数据看板（view_stats） ----------


@router.get("/stats", response_model=StatsResponse)
def get_stats(
    user: AuthUser = Depends(require_perm("view_stats")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """看板聚合：计数 + 近 14 天反馈分布 + 知识类型分布。"""
    with session_factory() as session:
        user_count = session.execute(select(func.count(User.id))).scalar_one()
        knowledge_count = session.execute(select(func.count(KnowledgeEntry.id))).scalar_one()
        pending_bad = session.execute(
            select(func.count(BadCase.id)).where(BadCase.status == "PENDING")
        ).scalar_one()
        pending_sug = session.execute(
            select(func.count(Suggestion.id)).where(Suggestion.status == "PENDING")
        ).scalar_one()
        adopted = session.execute(
            select(func.count(Suggestion.id)).where(Suggestion.status == "ADOPTED")
        ).scalar_one()
        rejected = session.execute(
            select(func.count(Suggestion.id)).where(Suggestion.status == "REJECTED")
        ).scalar_one()
        resolved = session.execute(
            select(func.count(BadCase.id)).where(BadCase.status == "RESOLVED")
        ).scalar_one()
        type_rows = session.execute(
            select(KnowledgeEntry.type, func.count(KnowledgeEntry.id)).group_by(KnowledgeEntry.type)
        ).all()
        cutoff = datetime.now(UTC) - timedelta(days=14)
        bad_dates = session.execute(
            select(BadCase.created_at).where(BadCase.created_at >= cutoff)
        ).scalars().all()
        sug_dates = session.execute(
            select(Suggestion.created_at).where(Suggestion.created_at >= cutoff)
        ).scalars().all()
    type_dist = {t: c for t, c in type_rows}
    # 近 14 天按日期补零
    by_day: dict[str, dict] = {}
    for i in range(14):
        d = (datetime.now(UTC) - timedelta(days=13 - i)).date().isoformat()
        by_day[d] = {"bad_case": 0, "suggestion": 0}
    for dt in bad_dates:
        by_day.setdefault(dt.date().isoformat(), {"bad_case": 0, "suggestion": 0})["bad_case"] += 1
    for dt in sug_dates:
        by_day.setdefault(dt.date().isoformat(), {"bad_case": 0, "suggestion": 0})["suggestion"] += 1
    return StatsResponse(
        user_count=user_count,
        knowledge_count=knowledge_count,
        pending_bad_cases=pending_bad,
        pending_suggestions=pending_sug,
        adopted=adopted,
        rejected=rejected,
        resolved=resolved,
        feedback_by_day=[{"date": d, **v} for d, v in by_day.items()],
        type_dist=type_dist,
    )


# ---------- M4 用户管理（user_mgmt） ----------


@router.get("/users", response_model=UserListResponse)
def list_users(
    user: AuthUser = Depends(require_perm("user_mgmt")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    with session_factory() as session:
        rows = session.execute(select(User).order_by(User.id)).scalars().all()
    return UserListResponse(
        items=[
            UserListItem(
                id=u.id,
                name=u.name,
                role=u.role,
                permissions=[p for p in u.permissions.split(",") if p],
                enabled=u.enabled,
                student_no=u.student_no,
            )
            for u in rows
        ]
    )


@router.post("/users", response_model=UserListItem)
def create_user(
    payload: UserCreateRequest,
    user: AuthUser = Depends(require_perm("user_mgmt")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    _validate_permissions(payload.role, payload.permissions)
    with session_factory() as session, session.begin():
        exists = session.get(User, payload.id)
        if exists is not None:
            raise HTTPException(status_code=409, detail="账号已存在")
        obj = User(
            id=payload.id,
            name=payload.name,
            role=payload.role,
            student_no=payload.student_no,
            dept=payload.dept,
            password_hash=hash_password(payload.password),
            permissions=",".join(payload.permissions),
            enabled=True,
        )
        session.add(obj)
    write_audit(
        session_factory,
        user_id=user.id,
        action="user_create",
        object_type="user",
        object_id=payload.id,
        detail=f"新增用户 {payload.id} ({payload.role})",
    )
    return UserListItem(
        id=obj.id, name=obj.name, role=obj.role,
        permissions=payload.permissions, enabled=True, student_no=obj.student_no,
    )


@router.put("/users/{uid}", response_model=UserListItem)
def update_user(
    uid: str,
    payload: UserUpdateRequest,
    user: AuthUser = Depends(require_perm("user_mgmt")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    _validate_permissions(payload.role, payload.permissions)
    with session_factory() as session, session.begin():
        obj = session.get(User, uid)
        if obj is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        # 对抗性审查 #3：admin 角色不可被禁用/降权（防锁死）
        if obj.role == "admin" and (not payload.enabled or payload.role != "admin"):
            raise HTTPException(status_code=403, detail="admin 账号不可被禁用或降权")
        obj.role = payload.role
        obj.permissions = ",".join(payload.permissions)
        obj.enabled = payload.enabled
    write_audit(
        session_factory,
        user_id=user.id,
        action="user_update",
        object_type="user",
        object_id=uid,
        detail=f"编辑用户 {uid}: role={payload.role} enabled={payload.enabled}",
    )
    return UserListItem(
        id=obj.id, name=obj.name, role=obj.role,
        permissions=payload.permissions, enabled=obj.enabled, student_no=obj.student_no,
    )


@router.post("/users/{uid}/reset-password", response_model=UserListItem)
def reset_password(
    uid: str,
    payload: ResetPasswordRequest,
    user: AuthUser = Depends(require_perm("user_mgmt")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    with session_factory() as session, session.begin():
        obj = session.get(User, uid)
        if obj is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        obj.password_hash = hash_password(payload.password)
    write_audit(
        session_factory,
        user_id=user.id,
        action="user_reset_password",
        object_type="user",
        object_id=uid,
        detail=f"重置密码 {uid}",
    )
    return UserListItem(
        id=obj.id, name=obj.name, role=obj.role,
        permissions=[p for p in obj.permissions.split(",") if p],
        enabled=obj.enabled, student_no=obj.student_no,
    )


# ---------- M4 日志管理（view_logs） ----------


@router.get("/logs", response_model=LogListResponse)
def list_logs(
    user_id: str = "",
    action: str = "",
    user: AuthUser = Depends(require_perm("view_logs")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """审计日志列表（操作人/动作筛选，按时间倒序，上限 200）。"""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    with session_factory() as session:
        rows = session.execute(stmt).scalars().all()
    return LogListResponse(
        items=[
            LogItem(
                id=r.id,
                user_id=r.user_id,
                action=r.action,
                object_type=r.object_type,
                object_id=r.object_id,
                detail=r.detail,
                created_at=r.created_at,
            )
            for r in rows
        ]
    )
