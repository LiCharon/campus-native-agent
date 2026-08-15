"""API 契约模型（M6）：请求/响应 pydantic 模型，与前端契约一一对应。

M1-T1：退役报修/投诉/工单/FAQ 模块后，仅保留 auth / chat 契约。
ChatResponse 的 ticket_id/ticket_status/ticket_type 字段由后续任务改造。
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
