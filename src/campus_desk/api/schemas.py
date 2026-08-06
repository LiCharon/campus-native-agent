"""API 契约模型（M6）：请求/响应 pydantic 模型，与前端契约一一对应。"""

from datetime import datetime

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    id: str
    name: str
    role: str
    dept: str | None = None
    student_no: str | None = None


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    user: UserInfo


class ChatRequest(BaseModel):
    thread_id: str
    msg: str


class ChatResponse(BaseModel):
    """orchestrator.turn 返回原样透出（字段可选——各路由分支字段集不同）。

    ticket_type 不在 turn 返回里：chat 路由在 ticket_id 非空时查库补上
    （前端工单卡片"类别"显示用）。
    """

    reply: str
    route: str
    pending_question: str | None = None
    ticket_id: int | None = None
    ticket_status: str | None = None
    ticket_type: str | None = None
    finished: bool | None = None
    tool_calls: list[str] = []
    status_events: list[str] = []
    outcome: str | None = None


class TicketSummary(BaseModel):
    id: int
    ticket_type: str
    category: str
    priority: str
    status: str
    building: str | None = None
    description: str
    created_at: datetime
    dept: str | None = None
    # 归属方（M6 验收补：前端操作按钮按 owner 显隐——staff/admin 列表
    # 里非自己的单不显示验收/撤回；staff 本就可见本部门单的 user_id）
    user_id: str


class TicketDetail(TicketSummary):
    user_id: str
    contact: str
    location: str | None = None
    repairman_id: str | None = None
    repairman_name: str | None = None
    escalation_count: int = 0
    escalated_at: datetime | None = None
    closed_at: datetime | None = None
    rating: int | None = None
    review_comment: str | None = None
    logs_count: int = 0


class TicketListResponse(BaseModel):
    items: list[TicketSummary]
    total: int


class StatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_category: dict[str, int]


class AssignRequest(BaseModel):
    repairman_id: str | None = None
    dept: str | None = None


class StaffInfo(BaseModel):
    """派单下拉候选 = repairmen 表（tickets.repairman_id 的外键目标）。

    M6 验收坑：原实现查 users 表（staff-001 等账号 id），写库违反
    fk_tickets_repairman_id_repairmen → 500。维修工实体在 repairmen 表。
    """

    id: str
    name: str
    dept: str | None = None
    trade: str | None = None
    on_duty: bool = True
