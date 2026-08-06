"""工单路由（M6）：列表/详情（RBAC 数据过滤）+ 验收/撤回 + 管理派单 + 看板聚合。

数据权限（需求 §8 已拍死）：
- student：仅自己创建的工单（越权与不存在统一 404，防枚举）
- staff/it_staff：仅本部门工单（tickets.dept == 用户 dept；未派单单对 staff 不可见）
- admin：全量
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from campus_desk.api.deps import AuthUser, get_current_user, get_session_factory, require_roles
from campus_desk.api.schemas import (
    AssignRequest,
    StaffInfo,
    StatsResponse,
    TicketDetail,
    TicketListResponse,
    TicketSummary,
)
from campus_desk.db.models import Repairman, Ticket, TicketLog, User
from campus_desk.db.session import SessionFactory
from campus_desk.state_machine.machine import TransitionError
from campus_desk.state_machine.transitions import TicketNotFound, apply_transition

router = APIRouter(prefix="/api", tags=["tickets"])

# 可查看工单的角色（student 单独过滤；看板接口用 require_roles 组合）
_MANAGE_ROLES = ("staff", "it_staff", "admin")


def _visible_filter(user: AuthUser, session_factory: SessionFactory):
    """RBAC 数据过滤条件：student → user_id；staff/it_staff → dept；admin → 全量。"""
    if user.role == "student":
        return Ticket.user_id == user.id
    if user.role in ("staff", "it_staff"):
        with session_factory() as session:
            dept = session.execute(select(User.dept).where(User.id == user.id)).scalar_one_or_none()
        return Ticket.dept == (dept or "")
    return None  # admin 全量


def _get_ticket_or_404(
    session_factory: SessionFactory, ticket_id: int, user: AuthUser
) -> Ticket:
    """按 RBAC 取工单；无权/不存在统一 404（防枚举）。"""
    with session_factory() as session:
        stmt = select(Ticket).where(Ticket.id == ticket_id)
        cond = _visible_filter(user, session_factory)
        if cond is not None:
            stmt = stmt.where(cond)
        ticket = session.execute(stmt).scalar_one_or_none()
        if ticket is None:
            raise HTTPException(status_code=404, detail="工单不存在")
        return ticket


def _summary(ticket: Ticket) -> TicketSummary:
    return TicketSummary(
        id=ticket.id,
        ticket_type=ticket.ticket_type,
        category=ticket.category,
        priority=ticket.priority,
        status=ticket.status,
        building=ticket.building,
        description=ticket.description,
        created_at=ticket.created_at,
        dept=ticket.dept,
        user_id=ticket.user_id,
    )


@router.get("/tickets", response_model=TicketListResponse)
def list_tickets(
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """工单列表（created_at 倒序）。student 只见自己的，staff 见本部门。"""
    with session_factory() as session:
        stmt = select(Ticket)
        cond = _visible_filter(user, session_factory)
        if cond is not None:
            stmt = stmt.where(cond)
        if status:
            stmt = stmt.where(Ticket.status == status)
        total = session.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = (
            session.execute(
                stmt.order_by(Ticket.created_at.desc()).limit(limit).offset(offset)
            )
            .scalars()
            .all()
        )
        return TicketListResponse(items=[_summary(t) for t in rows], total=total)


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(
    ticket_id: int,
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """工单详情：全字段 + 维修人姓名 + 审计日志条数。"""
    ticket = _get_ticket_or_404(session_factory, ticket_id, user)
    with session_factory() as session:
        repairman_name = (
            session.execute(
                select(User.name).where(User.id == ticket.repairman_id)
            ).scalar_one_or_none()
            if ticket.repairman_id
            else None
        )
        logs_count = session.execute(
            select(func.count()).select_from(TicketLog).where(TicketLog.ticket_id == ticket.id)
        ).scalar_one()
    return TicketDetail(
        **_summary(ticket).model_dump(),
        contact=ticket.contact,
        location=ticket.location,
        repairman_id=ticket.repairman_id,
        repairman_name=repairman_name,
        escalation_count=ticket.escalation_count,
        escalated_at=ticket.escalated_at,
        closed_at=ticket.closed_at,
        rating=ticket.rating,
        review_comment=ticket.review_comment,
        logs_count=logs_count,
    )


def _owner_action(
    session_factory: SessionFactory,
    ticket_id: int,
    event: str,
    user: AuthUser,
):
    """工单 owner 操作（验收/撤回）：RBAC 可见（404 防枚举）+ 归属校验（403）。

    M6 验收坑：原实现只做了可见性检查——staff 能看见本部门学生单，
    未校验归属导致 staff 可直接验收/撤回学生工单（越权）。现要求
    ticket.user_id == 操作者本人。
    """
    ticket = _get_ticket_or_404(session_factory, ticket_id, user)
    if ticket.user_id != user.id:
        raise HTTPException(status_code=403, detail="只能操作自己的工单")
    try:
        with session_factory() as session, session.begin():
            apply_transition(session, ticket_id, event, actor=user.id)
    except (TicketNotFound, TransitionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tickets/{ticket_id}/verify")
def verify_ticket(
    ticket_id: int,
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """学生验收（verify_ok，PENDING_VERIFY → CLOSED）。"""
    _owner_action(session_factory, ticket_id, "verify_ok", user)
    return {"ok": True}


@router.post("/tickets/{ticket_id}/cancel")
def cancel_ticket(
    ticket_id: int,
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """学生撤回（cancel，SUBMITTED/ASSIGNED → CANCELLED）。"""
    _owner_action(session_factory, ticket_id, "cancel", user)
    return {"ok": True}


@router.post("/admin/tickets/{ticket_id}/assign")
def assign_ticket(
    ticket_id: int,
    payload: AssignRequest,
    user: AuthUser = Depends(require_roles(*_MANAGE_ROLES)),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """派单（assign，SUBMITTED → ASSIGNED）：可带维修人/部门，其余交给自动派单。"""
    if not payload.repairman_id and not payload.dept:
        raise HTTPException(status_code=400, detail="需要维修人或部门")
    # 防御：repairman_id 必须存在于 repairmen 表（外键约束的报错 500 不如 400 明确；
    # M6 验收坑——下拉曾返回 users 表 id 导致违反 fk_tickets_repairman_id_repairmen）
    if payload.repairman_id:
        with session_factory() as session:
            exists = session.execute(
                select(Repairman.id).where(Repairman.id == payload.repairman_id)
            ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=400, detail=f"维修人不存在: {payload.repairman_id}")
    try:
        with session_factory() as session, session.begin():
            apply_transition(
                session,
                ticket_id,
                "assign",
                actor=user.id,
                repairman_id=payload.repairman_id,
                dept=payload.dept,
            )
    except (TicketNotFound, TransitionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/dashboard", response_model=StatsResponse)
def dashboard(
    user: AuthUser = Depends(require_roles(*_MANAGE_ROLES)),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """指标看板聚合（需求 §8 点名 ECharts）：总数 + 状态/优先级/类别分布。"""
    cond = _visible_filter(user, session_factory)

    def _grouped(column) -> dict[str, int]:
        stmt = select(column, func.count()).group_by(column)
        if cond is not None:
            stmt = stmt.where(cond)
        with session_factory() as session:
            return {str(k): int(v) for k, v in session.execute(stmt).all()}

    with session_factory() as session:
        stmt = select(func.count()).select_from(Ticket)
        if cond is not None:
            stmt = stmt.where(cond)
        total = session.execute(stmt).scalar_one()
    return StatsResponse(
        total=total,
        by_status=_grouped(Ticket.status),
        by_priority=_grouped(Ticket.priority),
        by_category=_grouped(Ticket.category),
    )


@router.get("/admin/staff", response_model=list[StaffInfo])
def list_staff(
    _user: AuthUser = Depends(require_roles(*_MANAGE_ROLES)),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """维修人下拉列表（派单弹窗用）：repairmen 表（tickets.repairman_id 的外键目标）。

    M6 验收坑：原实现查 users 表（staff-001 等账号），派单写 repairman_id
    违反 fk_tickets_repairman_id_repairmen → 500。维修工实体 = repairmen；
    只返回在岗（on_duty），与 RepairGraph 自动派单"在岗优先"口径一致。
    """
    with session_factory() as session:
        rows = (
            session.execute(
                select(Repairman.id, Repairman.name, Repairman.dept, Repairman.trade)
                .where(Repairman.on_duty.is_(True))
                .order_by(Repairman.id)
            )
            .all()
        )
    return [
        StaffInfo(id=r.id, name=r.name, dept=r.dept, trade=r.trade, on_duty=True)
        for r in rows
    ]
