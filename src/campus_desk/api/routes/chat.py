"""对话路由（M6 + M4）：POST /api/chat——user_id 取自 JWT，绝不信请求体。

M1-T8：删 ticket_id/ticket_status 休眠字段（M1 无工单概念），run_turn 新返回
仅透传 orchestrator 实际产出字段。
M4：响应加 sources（来源 chip 数据，Kimi 设计 §3.3）——知识命中 id 列表回查
知识库 → kb 来源（#K{id} {type}型 · {domain}）；工具调用 → tool 来源（工具名）。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select

from campus_desk.api.deps import (
    AuthUser,
    get_current_user,
    get_registry,
    get_session_factory,
)
from campus_desk.api.graphs import GraphRegistry, run_turn
from campus_desk.api.schemas import ChatRequest, ChatResponse, SourceItem
from campus_desk.db.models import KnowledgeEntry
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


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user: AuthUser = Depends(get_current_user),
    registry: GraphRegistry = Depends(get_registry),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """一轮对话：run_turn（锁内 entry 分流 + knowledge 图）→ ChatResponse。

    同步 def（线程池执行）：turn 内部是同步 LangGraph 调用 + LLM 网络 IO，
    async def 会阻塞事件循环（FastAPI 文档明确同步阻塞代码走 def）。
    """
    result = run_turn(registry, user.id, payload.thread_id, payload.msg)
    return ChatResponse(
        reply=result["reply"],
        route=result["route"],
        pending_question=result.get("pending_question"),
        finished=result.get("finished"),
        outcome=result.get("outcome"),
        tool_calls=result.get("tool_calls", []),
        status_events=result.get("status_events", []),
        sources=_build_sources(result, session_factory),
    )
