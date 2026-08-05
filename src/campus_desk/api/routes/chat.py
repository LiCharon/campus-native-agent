"""对话路由（M6）：POST /api/chat——user_id 取自 JWT，绝不信请求体。"""

from fastapi import APIRouter, Depends
from sqlalchemy import select

from campus_desk.api.deps import AuthUser, get_current_user, get_registry, get_session_factory
from campus_desk.api.graphs import GraphRegistry, run_turn
from campus_desk.api.schemas import ChatRequest, ChatResponse
from campus_desk.db.models import Ticket
from campus_desk.db.session import SessionFactory

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user: AuthUser = Depends(get_current_user),
    registry: GraphRegistry = Depends(get_registry),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """一轮对话：turn 返回 dict 原样透出。

    同步 def（线程池执行）：turn 内部是同步 LangGraph 调用 + LLM 网络 IO，
    async def 会阻塞事件循环（FastAPI 文档明确同步阻塞代码走 def）。
    """
    result = run_turn(registry, user.id, payload.thread_id, payload.msg)
    ticket_type = None
    if result.get("ticket_id"):
        # 补 ticket_type（turn 返回不含；前端工单卡片"类别"显示用）
        with session_factory() as session:
            ticket_type = session.execute(
                select(Ticket.ticket_type).where(Ticket.id == result["ticket_id"])
            ).scalar_one_or_none()
    return ChatResponse(
        reply=result["reply"],
        route=result["route"],
        pending_question=result.get("pending_question"),
        ticket_id=result.get("ticket_id"),
        ticket_status=result.get("ticket_status"),
        ticket_type=ticket_type,
        finished=result.get("finished"),
        tool_calls=result.get("tool_calls", []),
        status_events=result.get("status_events", []),
        outcome=result.get("outcome"),
    )
