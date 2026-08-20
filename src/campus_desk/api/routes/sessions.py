"""会话路由（M5-ZJUT 服务端化）：/api/sessions 增删改查 + /api/sessions/{id}/messages 消息历史。

设计要点：
- 归属校验（硬约束）：所有按 id 的操作先查 conversation，非当前用户 → 404
  （不泄露存在性，跨账号操作统一"会话不存在"）
- 会话生命周期归服务端：id/thread_id 服务端 UUID 生成；/api/chat 要求
  thread_id 属于当前用户已建会话（严格模式，无懒创建）
- DELETE 写审计（grill 定案：高危操作留痕；创建/改名不写）
- 消息 sources/tool_calls/status_events 存 JSON 文本，读时解析
"""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from campus_desk.api.deps import AuthUser, get_current_user, get_session_factory
from campus_desk.api.schemas import (
    MessageItem,
    MessageListResponse,
    SessionItem,
    SessionListResponse,
    SessionUpdateRequest,
    SourceItem,
)
from campus_desk.audit import write_audit
from campus_desk.db.models import Conversation, Message
from campus_desk.db.session import SessionFactory

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _get_owned(session, cid: str, user_id: str) -> Conversation:
    """归属校验：查不到或非本人 → 404（不泄露会话存在性）。"""
    conv = session.get(Conversation, cid)
    if conv is None or conv.user_id != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


def _to_item(conv: Conversation) -> SessionItem:
    return SessionItem(
        id=conv.id,
        thread_id=conv.thread_id,
        title=conv.title,
        title_source=conv.title_source,
        handoff=conv.handoff,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


def _parse_json_list(raw: str) -> list:
    """消息 JSON 文本列解析（脏数据/空串兜底为 []）。"""
    if not raw:
        return []
    import json

    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _to_message_item(m: Message) -> MessageItem:
    return MessageItem(
        id=m.id,
        role=m.role,
        content=m.content,
        route=m.route,
        outcome=m.outcome,
        pending_question=m.pending_question,
        tool_calls=_parse_json_list(m.tool_calls),
        status_events=_parse_json_list(m.status_events),
        sources=[SourceItem.model_validate(s) for s in _parse_json_list(m.sources)],
        error=m.error,
        feedback_submitted=m.feedback_submitted,
        created_at=m.created_at,
    )


@router.get("", response_model=SessionListResponse)
def list_sessions(
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """当前用户会话列表（不含消息），按 updated_at 降序（最新活动在前）。"""
    with session_factory() as session:
        rows = (
            session.execute(
                select(Conversation)
                .where(Conversation.user_id == user.id)
                .order_by(Conversation.updated_at.desc())
            )
            .scalars()
            .all()
        )
        return SessionListResponse(items=[_to_item(c) for c in rows])


@router.post("", response_model=SessionItem)
def create_session(
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """新建会话：id（业务 id）/thread_id（LangGraph checkpointer key）服务端生成。"""
    conv = Conversation(
        id=uuid4().hex,
        thread_id=str(uuid4()),
        user_id=user.id,
        title="新对话",
        title_source="auto",
        handoff="none",
    )
    with session_factory() as session, session.begin():
        session.add(conv)
    return _to_item(conv)


@router.patch("/{cid}", response_model=SessionItem)
def update_session(
    cid: str,
    payload: SessionUpdateRequest,
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """更新会话：title（置 title_source=manual）/ handoff（转人工状态）。"""
    with session_factory() as session, session.begin():
        conv = _get_owned(session, cid, user.id)
        if payload.title is not None:
            title = payload.title.strip()
            conv.title = title or "新对话"
            conv.title_source = "manual"
        if payload.handoff is not None:
            conv.handoff = payload.handoff
    return _to_item(conv)


@router.delete("/{cid}")
def delete_session(
    cid: str,
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """删除会话（消息级联删除）；高危操作写审计日志。"""
    with session_factory() as session, session.begin():
        conv = _get_owned(session, cid, user.id)
        title = conv.title
        session.delete(conv)
    write_audit(
        session_factory,
        user_id=user.id,
        action="conversation_delete",
        object_type="conversation",
        object_id=cid,
        detail=title,
    )
    return {"ok": True}


@router.get("/{cid}/messages", response_model=MessageListResponse)
def list_messages(
    cid: str,
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """会话消息历史（按 id 升序），sources/tool_calls/status_events 已解析。"""
    with session_factory() as session:
        _get_owned(session, cid, user.id)
        rows = (
            session.execute(
                select(Message)
                .where(Message.conversation_id == cid)
                .order_by(Message.id)
            )
            .scalars()
            .all()
        )
        return MessageListResponse(items=[_to_message_item(m) for m in rows])
