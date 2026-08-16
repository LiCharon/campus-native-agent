"""API 契约模型（M6）：请求/响应 pydantic 模型，与前端契约一一对应。

M1-T1：退役报修/投诉/工单/FAQ 模块后，仅保留 auth / chat 契约。
M1-T8：ChatResponse 删 ticket_id/ticket_status/ticket_type（M1 无工单概念，
仅保留 orchestrator.turn 实际产出字段）。
M3：进化闭环契约（feedback 双通道 + admin 审查），见 ZJUT_DESIGN §5.5。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


# ---------- M3 进化闭环（设计 §5.5 双通道 + 管理员审查） ----------

def _strip_nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


class FeedbackBadCaseRequest(BaseModel):
    """对话页"没解决"手动反馈（进化闭环①）：写 bad_cases。"""

    thread_id: str
    question: str
    reply: str = ""
    note: str = ""

    _question = field_validator("question")(_strip_nonblank)


class FeedbackSuggestionRequest(BaseModel):
    """对话页"问题没答案"提议（进化闭环②）：写 suggestions。"""

    question: str
    note: str = ""

    _question = field_validator("question")(_strip_nonblank)


class FeedbackResponse(BaseModel):
    id: int


# 审查来源：bad_cases（未解决反馈）/ suggestions（用户提议）
ReviewKind = Literal["bad_cases", "suggestions"]

# 知识库六领域 + type 三型（与 models.py/seed.py 同源）
KNOWLEDGE_DOMAIN = Literal["教务", "后勤", "图书馆", "IT", "证件", "生活"]
KNOWLEDGE_TYPE = Literal["info", "process", "index"]


class ReviewItem(BaseModel):
    """待审列表项：来源行 + keywords 预填建议（管理员可编辑）。

    note 可空：存量 bad_cases（M1/M2 转人工自动沉淀）迁移加列后为 NULL。
    """

    id: int
    user_id: str
    question: str
    reply: str = ""
    note: str | None = None
    status: str
    created_at: datetime
    suggested_keywords: str = ""


class ReviewListResponse(BaseModel):
    items: list[ReviewItem]


class AdoptRequest(BaseModel):
    """补入知识库请求（adopt）：管理员填写，keywords/answer 非空硬校验。"""

    domain: KNOWLEDGE_DOMAIN
    type: KNOWLEDGE_TYPE
    keywords: str
    answer: str = Field(min_length=1)

    _keywords = field_validator("keywords")(_strip_nonblank)
    _answer = field_validator("answer")(_strip_nonblank)


class ReviewActionResponse(BaseModel):
    id: int
    status: str
