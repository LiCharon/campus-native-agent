"""管理路由（M3 审查 + M4 管理特权）：审查补入/驳回 + 知识浏览 + 数据看板 + 用户管理 + 日志。

权限（M4 权限位）：
- reviews（审查补入/驳回）：require_perm("kb_review")——支持被授 kb_review 的 cs_staff
- knowledge（浏览）/ stats / users / logs：对应 view_stats/user_mgmt/view_logs 位

保护（对抗性审查 #3/#4）：
- admin 角色用户不可被禁用/降权（防止锁死系统）
- student 不允许携带附加权限位（授予对象限 cs_staff/admin）

审计：adopt/dismiss/users 操作写 audit_logs（旁路）。
"""

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from campus_desk import rate_limit
from campus_desk.api.deps import AuthUser, get_session_factory, require_perm
from campus_desk.api.schemas import (
    AdoptRequest,
    BusinessStats,
    KnowledgeCreateRequest,
    KnowledgeItem,
    KnowledgeListResponse,
    KnowledgeUpdateRequest,
    LogItem,
    LogListResponse,
    PermissionItem,
    PermissionListResponse,
    ResetPasswordRequest,
    ReviewActionResponse,
    ReviewItem,
    ReviewKind,
    ReviewListResponse,
    RoleItem,
    RoleListResponse,
    SourceItem,
    StatsResponse,
    UserCreateRequest,
    UserListItem,
    UserListResponse,
    UserUpdateRequest,
)
from campus_desk.audit import write_audit
from campus_desk.db.models import (
    AuditLog,
    BadCase,
    Conversation,
    KnowledgeEntry,
    Message,
    Permission,
    Role,
    Suggestion,
    User,
)
from campus_desk.db.session import SessionFactory
from campus_desk.knowledge import vector_store
from campus_desk.knowledge.suggest import suggest_keywords
from campus_desk.profile.extract import extract_domains
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


def _validate_permissions(
    role: str, permissions: list[str], session_factory: SessionFactory
) -> None:
    """附加权限位校验（M6 查库）：role 须在 roles 表、permissions 须在 permissions 表；
    student 不允许携带附加位（对抗性审查 #4）。"""
    with session_factory() as session:
        valid_roles = {r for (r,) in session.execute(select(Role.id))}
        valid_perms = {p for (p,) in session.execute(select(Permission.id))}
    if role not in valid_roles:
        raise HTTPException(status_code=422, detail=f"未知角色: {role}")
    unknown = [p for p in permissions if p not in valid_perms]
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
                select(model).where(model.status == status).order_by(model.created_at.desc())
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
        new_entry = KnowledgeEntry(
            domain=payload.domain,
            keywords=payload.keywords,
            question=question,
            type=payload.type,
            answer=payload.answer,
        )
        session.add(new_entry)
        session.flush()
        new_id = new_entry.id
        row = session.get(_SOURCE_MODEL[kind], rid)
        row.status = _SOURCE_ACTION_STATUS[kind]["adopt"]
        new_status = row.status
    vector_store.sync_entry(
        session_factory,
        {
            "id": new_id,
            "domain": payload.domain,
            "keywords": payload.keywords,
            "question": question,
            "type": payload.type,
            "answer": payload.answer,
        },
    )
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


# ---------- M9 知识条目增改删（kb_review） ----------


@router.post("/knowledge", response_model=KnowledgeItem)
def create_knowledge(
    payload: KnowledgeCreateRequest,
    user: AuthUser = Depends(require_perm("kb_review")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """新建知识条目：建行 → flush 取 id → 同步向量（MySQL 稠密 + Qdrant 点）→ 审计。"""
    with session_factory() as session, session.begin():
        entry = KnowledgeEntry(
            domain=payload.domain,
            keywords=payload.keywords,
            question=payload.question,
            type=payload.type,
            answer=payload.answer,
        )
        session.add(entry)
        session.flush()
        new_id = entry.id
    vector_store.sync_entry(
        session_factory,
        {
            "id": new_id,
            "domain": payload.domain,
            "keywords": payload.keywords,
            "question": payload.question,
            "type": payload.type,
            "answer": payload.answer,
        },
    )
    write_audit(
        session_factory,
        user_id=user.id,
        action="kb_create",
        object_type="knowledge",
        object_id=new_id,
        detail=payload.question[:60],
    )
    return KnowledgeItem(
        id=new_id,
        domain=payload.domain,
        keywords=payload.keywords,
        question=payload.question,
        type=payload.type,
        answer=payload.answer,
    )


@router.put("/knowledge/{kid}", response_model=KnowledgeItem)
def update_knowledge(
    kid: int,
    payload: KnowledgeUpdateRequest,
    user: AuthUser = Depends(require_perm("kb_review")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """编辑知识条目：取行（404）→ 更新字段 → 同步向量 → 审计。"""
    with session_factory() as session, session.begin():
        row = session.get(KnowledgeEntry, kid)
        if row is None:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        row.domain = payload.domain
        row.keywords = payload.keywords
        row.question = payload.question
        row.type = payload.type
        row.answer = payload.answer
    vector_store.sync_entry(
        session_factory,
        {
            "id": kid,
            "domain": payload.domain,
            "keywords": payload.keywords,
            "question": payload.question,
            "type": payload.type,
            "answer": payload.answer,
        },
    )
    write_audit(
        session_factory,
        user_id=user.id,
        action="kb_update",
        object_type="knowledge",
        object_id=kid,
        detail=payload.question[:60],
    )
    return KnowledgeItem(
        id=kid,
        domain=payload.domain,
        keywords=payload.keywords,
        question=payload.question,
        type=payload.type,
        answer=payload.answer,
    )


@router.delete("/knowledge/{kid}", response_model=KnowledgeItem)
def delete_knowledge(
    kid: int,
    user: AuthUser = Depends(require_perm("kb_review")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """删除知识条目（硬删）：取行（404）→ 删行 → 清向量 → 审计。"""
    with session_factory() as session, session.begin():
        row = session.get(KnowledgeEntry, kid)
        if row is None:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        deleted = KnowledgeItem(
            id=row.id,
            domain=row.domain,
            keywords=row.keywords,
            question=row.question,
            type=row.type,
            answer=row.answer,
        )
        session.delete(row)
    vector_store.delete_entry_vector(session_factory, kid)
    write_audit(
        session_factory,
        user_id=user.id,
        action="kb_delete",
        object_type="knowledge",
        object_id=kid,
        detail=deleted.question[:60],
    )
    return deleted


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
        bad_dates = (
            session.execute(select(BadCase.created_at).where(BadCase.created_at >= cutoff))
            .scalars()
            .all()
        )
        sug_dates = (
            session.execute(select(Suggestion.created_at).where(Suggestion.created_at >= cutoff))
            .scalars()
            .all()
        )
        # M8 业务指标：会话/消息/bad_cases 聚合（口径见 docs/plans/M8_PLAN.md §3）
        conv_total = session.execute(select(func.count(Conversation.id))).scalar_one()
        transfer_total = session.execute(
            select(func.count(Conversation.id)).where(Conversation.handoff == "human")
        ).scalar_one()
        user_total = session.execute(
            select(func.count(Message.id)).where(Message.role == "user")
        ).scalar_one()
        # 手动"没解决"通道（reply 非空；转人工自动沉淀 reply 为空天然排除），按 thread_id 去重
        manual_bad_conv = session.execute(
            select(func.count(func.distinct(BadCase.thread_id))).where(BadCase.reply != "")
        ).scalar_one()
        # 每会话首条/末条 assistant 消息（created_at+id 升序，分组取首末）
        assistant_rows = session.execute(
            select(Message.conversation_id, Message.outcome, Message.pending_question)
            .where(Message.role == "assistant")
            .order_by(Message.conversation_id, Message.created_at, Message.id)
        ).all()
        source_rows = (
            session.execute(select(Message.sources).where(Message.role == "assistant"))
            .scalars()
            .all()
        )
    # ---- M8 业务指标组装（除零保护：0 会话 → 全 0.0） ----
    first_by_conv: dict[str, tuple] = {}
    last_by_conv: dict[str, tuple] = {}
    for cid, outcome, pending in assistant_rows:
        first_by_conv.setdefault(cid, (outcome, pending))
        last_by_conv[cid] = (outcome, pending)
    first_turn_answer = sum(
        1 for outcome, pending in first_by_conv.values() if outcome == "answer" and not pending
    )
    completion = sum(1 for outcome, _ in last_by_conv.values() if outcome == "answer")
    # domain 分布：sources JSON → SourceItem → extract_domains（复用 M7 画像解析，脏数据跳过）
    domain_dist: dict[str, int] = {}
    for raw in source_rows:
        try:
            items = [SourceItem(**x) for x in json.loads(raw or "[]")]
        except (TypeError, ValueError):
            continue
        for d in extract_domains(items):
            domain_dist[d] = domain_dist.get(d, 0) + 1
    if conv_total == 0:
        business = BusinessStats(
            conversation_count=0,
            transfer_rate=0.0,
            first_turn_answer_rate=0.0,
            completion_rate=0.0,
            negative_feedback_rate=0.0,
            avg_turns=0.0,
            domain_dist={},
        )
    else:
        business = BusinessStats(
            conversation_count=conv_total,
            transfer_rate=transfer_total / conv_total,
            first_turn_answer_rate=first_turn_answer / conv_total,
            completion_rate=completion / conv_total,
            negative_feedback_rate=manual_bad_conv / conv_total,
            avg_turns=user_total / conv_total,
            domain_dist=domain_dist,
        )
    type_dist = {t: c for t, c in type_rows}
    # 近 14 天按日期补零
    by_day: dict[str, dict] = {}
    for i in range(14):
        d = (datetime.now(UTC) - timedelta(days=13 - i)).date().isoformat()
        by_day[d] = {"bad_case": 0, "suggestion": 0}
    for dt in bad_dates:
        by_day.setdefault(dt.date().isoformat(), {"bad_case": 0, "suggestion": 0})["bad_case"] += 1
    for dt in sug_dates:
        by_day.setdefault(dt.date().isoformat(), {"bad_case": 0, "suggestion": 0})[
            "suggestion"
        ] += 1
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
        business=business,
    )


# ---------- M6 RBAC 只读接口（user_mgmt）：角色/权限下拉查库 ----------


@router.get("/roles", response_model=RoleListResponse)
def list_roles(
    user: AuthUser = Depends(require_perm("user_mgmt")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    with session_factory() as session:
        rows = session.execute(select(Role).order_by(Role.id)).scalars().all()
    return RoleListResponse(items=[RoleItem(id=r.id, name=r.name) for r in rows])


@router.get("/permissions", response_model=PermissionListResponse)
def list_permissions(
    user: AuthUser = Depends(require_perm("user_mgmt")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    with session_factory() as session:
        rows = session.execute(select(Permission).order_by(Permission.id)).scalars().all()
    return PermissionListResponse(items=[PermissionItem(id=p.id, name=p.name) for p in rows])


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
    _validate_permissions(payload.role, payload.permissions, session_factory)
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
        id=obj.id,
        name=obj.name,
        role=obj.role,
        permissions=payload.permissions,
        enabled=True,
        student_no=obj.student_no,
    )


@router.put("/users/{uid}", response_model=UserListItem)
def update_user(
    uid: str,
    payload: UserUpdateRequest,
    user: AuthUser = Depends(require_perm("user_mgmt")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    _validate_permissions(payload.role, payload.permissions, session_factory)
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
        id=obj.id,
        name=obj.name,
        role=obj.role,
        permissions=payload.permissions,
        enabled=obj.enabled,
        student_no=obj.student_no,
    )


@router.post("/users/{uid}/unlock", response_model=UserListItem)
def unlock_login(
    uid: str,
    user: AuthUser = Depends(require_perm("user_mgmt")),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """M15A-③ 解除登录锁定：清空该用户的失败计数与锁定时长。

    存在的必要性：账号锁本身可被反向利用——任何人连错 5 次就能把真管理员
    锁死 15 分钟，没有解锁通道的话这是比暴力破解更现实的事故。
    """
    with session_factory() as session:
        obj = session.get(User, uid)
        if obj is None:
            raise HTTPException(status_code=404, detail="用户不存在")
    cleared = rate_limit.unlock(uid)
    write_audit(
        session_factory,
        user_id=user.id,
        action="login_unlocked",
        object_type="user",
        object_id=uid,
        detail=f"解除登录锁定 {uid}" + ("" if cleared else "（该账号原本未锁定）"),
    )
    return UserListItem(
        id=obj.id,
        name=obj.name,
        role=obj.role,
        permissions=[p for p in obj.permissions.split(",") if p],
        enabled=obj.enabled,
        student_no=obj.student_no,
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
        id=obj.id,
        name=obj.name,
        role=obj.role,
        permissions=[p for p in obj.permissions.split(",") if p],
        enabled=obj.enabled,
        student_no=obj.student_no,
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
