"""对话路由（M6 + M4 + M5-ZJUT）：POST /api/chat——user_id 取自 JWT，绝不信请求体。

M1-T8：删 ticket_id/ticket_status 休眠字段（M1 无工单概念），run_turn 新返回
仅透传 orchestrator 实际产出字段。
M4：响应加 sources（来源 chip 数据，Kimi 设计 §3.3）——知识命中 id 列表回查
知识库 → kb 来源（#K{id} {type}型 · {domain}）；工具调用 → tool 来源（工具名）。
M5-ZJUT：会话服务端化——
- thread_id 归属校验（严格模式）：必须属于当前用户已建会话，否则 404（无懒创建）
- 落库：user 消息先落（独立事务）→ run_turn（锁内 LLM 网络 IO，不占事务）
  → assistant 消息再落（含 route/outcome/pending_question/sources 等 JSON 列）
- 自动标题：title_source=auto 且 title="新对话" 时取首条用户消息去空白前 12 字
  （与旧前端语义等价：仅首条、手动改名不覆盖）
- 显式 touch conversation.updated_at（SQLAlchemy onupdate 仅行被 UPDATE 时触发）
"""

import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from campus_desk.api.deps import (
    AuthUser,
    get_current_user,
    get_registry,
    get_session_factory,
)
from campus_desk.api.graphs import GraphRegistry, run_turn
from campus_desk.api.schemas import ChatRequest, ChatResponse, SourceItem
from campus_desk.db.models import Conversation, KnowledgeEntry, Message
from campus_desk.db.session import SessionFactory

router = APIRouter(prefix="/api", tags=["chat"])


def _build_sources(result: dict, session_factory: SessionFactory) -> list[SourceItem]:
    """来源 chip 列表：knowledge 命中（hits 为 id 列表，回查条目）+ tool 调用。"""
    sources: list[SourceItem] = []
    hit_ids = result.get("hits", [])
    if hit_ids:
        with session_factory() as session:
            rows = (
                session.execute(select(KnowledgeEntry).where(KnowledgeEntry.id.in_(hit_ids)))
                .scalars()
                .all()
            )
        for r in rows:
            sources.append(
                SourceItem(
                    type="kb",
                    label="知识库",
                    ref_id=f"#K{r.id}",
                    detail=f"{r.type}型 · {r.domain}",
                )
            )
    for tool in result.get("tool_calls", []):
        sources.append(SourceItem(type="tool", label="工具查询", ref_id="", detail=tool))
    return sources


def _auto_title(conv: Conversation, msg: str) -> bool:
    """自动标题（首条消息前 12 字）：仅 title_source=auto 且 title=新对话 时生效。"""
    if conv.title_source != "auto" or conv.title != "新对话":
        return False
    raw = re.sub(r"\s+", "", msg)
    if not raw:
        return False
    conv.title = raw[:12] + "…" if len(raw) > 12 else raw
    return True


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user: AuthUser = Depends(get_current_user),
    registry: GraphRegistry = Depends(get_registry),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """一轮对话：归属校验 + 用户消息落库 → run_turn（锁内 entry 分流）→ assistant 落库。

    同步 def（线程池执行）：turn 内部是同步 LangGraph 调用 + LLM 网络 IO，
    async def 会阻塞事件循环（FastAPI 文档明确同步阻塞代码走 def）。
    """
    # 1. 归属校验 + user 消息落库（独立事务，发送中刷新用户消息不丢）
    with session_factory() as session, session.begin():
        conv = (
            session.execute(
                select(Conversation).where(
                    Conversation.thread_id == payload.thread_id,
                    Conversation.user_id == user.id,
                )
            )
            .scalars()
            .first()
        )
        if conv is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        session.add(Message(conversation_id=conv.id, role="user", content=payload.msg))
        _auto_title(conv, payload.msg)
        conv.updated_at = datetime.now(UTC)  # 显式 touch：更新列表排序

    # 2. run_turn（锁内，LLM 网络 IO 不占 DB 事务）
    result = run_turn(registry, user.id, payload.thread_id, payload.msg)
    sources = _build_sources(result, session_factory)

    # 3. assistant 消息落库（独立事务）
    with session_factory() as session, session.begin():
        conv = (
            session.execute(
                select(Conversation).where(
                    Conversation.thread_id == payload.thread_id,
                    Conversation.user_id == user.id,
                )
            )
            .scalars()
            .first()
        )
        if conv is not None:
            session.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=result["reply"],
                    route=result["route"],
                    outcome=result.get("outcome"),
                    pending_question=result.get("pending_question"),
                    tool_calls=json.dumps(result.get("tool_calls", []), ensure_ascii=False),
                    status_events=json.dumps(
                        result.get("status_events", []), ensure_ascii=False
                    ),
                    sources=json.dumps(
                        [s.model_dump() for s in sources], ensure_ascii=False
                    ),
                )
            )
            conv.updated_at = datetime.now(UTC)

    return ChatResponse(
        reply=result["reply"],
        route=result["route"],
        pending_question=result.get("pending_question"),
        finished=result.get("finished"),
        outcome=result.get("outcome"),
        tool_calls=result.get("tool_calls", []),
        status_events=result.get("status_events", []),
        sources=sources,
    )
