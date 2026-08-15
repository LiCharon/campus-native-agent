"""API 契约模型（M6）：请求/响应 pydantic 模型，与前端契约一一对应。

M1-T1：退役报修/投诉/工单/FAQ 模块后，仅保留 auth / chat 契约。
M1-T8：ChatResponse 删 ticket_id/ticket_status/ticket_type（M1 无工单概念，
仅保留 orchestrator.turn 实际产出字段）。
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
    reply: str
    route: str
    pending_question: str | None = None
    finished: bool | None = None
    outcome: str | None = None
    tool_calls: list[str] = []
    status_events: list[str] = []
