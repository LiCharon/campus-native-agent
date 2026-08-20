"""API 契约模型（M6）：请求/响应 pydantic 模型，与前端契约一一对应。

M1-T1：退役报修/投诉/工单/FAQ 模块后，仅保留 auth / chat 契约。
M1-T8：ChatResponse 删 ticket_id/ticket_status/ticket_type（M1 无工单概念，
仅保留 orchestrator.turn 实际产出字段）。
M3：进化闭环契约（feedback 双通道 + admin 审查），见 docs/design/ZJUT_DESIGN.md §5.5。
M5-ZJUT：会话契约（Session* / MessageItem），会话列表/消息历史/重命名/删除/转人工状态。
M6-ZJUT：RBAC 只读契约（RoleItem / PermissionItem），admin 用户页角色/权限下拉查库。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    id: str
    name: str
    role: str
    dept: str | None = None
    student_no: str | None = None
    permissions: list[str] = []  # M4：最终权限并集（角色默认 ∪ 附加位）


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    user: UserInfo


class ChatRequest(BaseModel):
    thread_id: str
    msg: str


class SourceItem(BaseModel):
    """来源 chip（M4，Kimi 设计 §3.3）：工具查询 / 知识库引用标注。"""

    type: Literal["tool", "kb"]
    label: str
    ref_id: str = ""
    detail: str = ""


class ChatResponse(BaseModel):
    reply: str
    route: str
    pending_question: str | None = None
    finished: bool | None = None
    outcome: str | None = None
    tool_calls: list[str] = []
    status_events: list[str] = []
    sources: list[SourceItem] = []  # M4：来源 chip 数据（工具查询/知识库命中）


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

# 知识库 11 领域 + type 三型（与 models.py/seed.py/AdminReview.vue 同源）
KNOWLEDGE_DOMAIN = Literal[
    "教务",
    "图书馆",
    "网络与IT",
    "校园卡与证件",
    "住宿后勤",
    "奖助",
    "医疗健康",
    "社团与活动",
    "就业与职业发展",
    "安全与保卫",
    "生活服务",
]
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


# ---------- M4 管理特权（用户/日志/看板/知识浏览） ----------


class UserCreateRequest(BaseModel):
    """新增用户（user_mgmt）：初始密码必填。"""

    id: str
    name: str
    # M6：改为 str，运行时查 roles 表校验存在性（角色下拉查库后值动态）
    role: str
    student_no: str | None = None
    dept: str | None = None
    password: str = Field(min_length=6)
    permissions: list[str] = []

    _id = field_validator("id")(_strip_nonblank)
    _name = field_validator("name")(_strip_nonblank)


class UserUpdateRequest(BaseModel):
    """编辑用户：role/permissions/enabled。admin 账号禁止降权/禁用（对抗性审查 #3）。"""

    # M6：改为 str，运行时查 roles 表校验存在性（角色下拉查库后值动态）
    role: str
    permissions: list[str] = []
    enabled: bool = True


class UserListItem(BaseModel):
    id: str
    name: str
    role: str
    permissions: list[str]
    enabled: bool
    student_no: str | None = None


class UserListResponse(BaseModel):
    items: list[UserListItem]


# ---------- M6-ZJUT RBAC 只读契约（admin 用户页下拉查库） ----------


class RoleItem(BaseModel):
    id: str
    name: str


class RoleListResponse(BaseModel):
    items: list[RoleItem]


class PermissionItem(BaseModel):
    id: str
    name: str


class PermissionListResponse(BaseModel):
    items: list[PermissionItem]


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=6)


class KnowledgeItem(BaseModel):
    id: int
    domain: str
    keywords: str
    question: str
    type: str
    answer: str


class KnowledgeListResponse(BaseModel):
    items: list[KnowledgeItem]


class StatsResponse(BaseModel):
    user_count: int
    knowledge_count: int
    pending_bad_cases: int
    pending_suggestions: int
    adopted: int
    rejected: int
    resolved: int  # bad_cases RESOLVED 总数（审查 + 客服两路径）
    feedback_by_day: list[dict]  # [{date, bad_case, suggestion}] 近 14 天
    type_dist: dict  # {info: n, process: n, index: n}


class LogItem(BaseModel):
    id: int
    user_id: str
    action: str
    object_type: str
    object_id: str = ""
    detail: str = ""
    created_at: datetime


class LogListResponse(BaseModel):
    items: list[LogItem]


# ---------- M5-ZJUT 会话契约（服务端会话/消息/转人工状态） ----------

# 转人工三态（与前端 useChat.js HANDOFF 同源）
HandoffState = Literal["none", "transferring", "human"]


class SessionItem(BaseModel):
    """会话列表项（不含消息——消息走 GET /sessions/{id}/messages）。"""

    id: str
    thread_id: str
    title: str
    title_source: Literal["auto", "manual"] = "auto"
    handoff: HandoffState = "none"
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    items: list[SessionItem]


class SessionUpdateRequest(BaseModel):
    """会话更新：title（重命名，置 title_source=manual）/ handoff（转人工状态）。

    至少一项必填（model_validator 校验）；两者可同时更新。
    title 限长 64 与 conversations.title 列宽一致（超长 MySQL 严格模式 500）。
    """

    title: str | None = Field(default=None, max_length=64)
    handoff: HandoffState | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> SessionUpdateRequest:
        if self.title is None and self.handoff is None:
            raise ValueError("title 与 handoff 至少提供一项")
        return self


class MessageItem(BaseModel):
    """消息历史项：sources/tool_calls/status_events 为解析后的 list。

    error/feedback_submitted 保留（前端反馈状态与失败标记的持久化位，
    当前仅 feedback_submitted 会被写回）。
    """

    id: int
    role: Literal["user", "assistant", "system"]
    content: str
    route: str | None = None
    outcome: str | None = None
    pending_question: str | None = None
    tool_calls: list[str] = []
    status_events: list[str] = []
    sources: list[SourceItem] = []
    error: bool = False
    feedback_submitted: bool = False
    created_at: datetime


class MessageListResponse(BaseModel):
    items: list[MessageItem]
