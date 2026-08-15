"""API 契约模型（M6）：请求/响应 pydantic 模型，与前端契约一一对应。

M1-T1：退役报修/投诉/工单/FAQ 模块后，仅保留 auth / chat 契约。
M1-T2-fix：ChatResponse 删 ticket_type（tickets 已退役）；ticket_id/ticket_status
由 T8 精简（M1 无工单概念）。
"""

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
    """orchestrator.turn 返回原样透出（字段可选——各路由分支字段集不同）。"""

    reply: str
    route: str
    pending_question: str | None = None
    ticket_id: int | None = None
    ticket_status: str | None = None
    finished: bool | None = None
    tool_calls: list[str] = []
    status_events: list[str] = []
    outcome: str | None = None
