"""对话路由（M6）：POST /api/chat——user_id 取自 JWT，绝不信请求体。

M1-T8：删 ticket_id/ticket_status 休眠字段（M1 无工单概念），run_turn 新返回
仅透传 orchestrator 实际产出字段。
"""

from fastapi import APIRouter, Depends

from campus_desk.api.deps import AuthUser, get_current_user, get_registry
from campus_desk.api.graphs import GraphRegistry, run_turn
from campus_desk.api.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user: AuthUser = Depends(get_current_user),
    registry: GraphRegistry = Depends(get_registry),
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
    )
