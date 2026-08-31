"""进化闭环反馈路由（M3，设计 §5.5 双通道）：学生反馈/提议落库。

- POST /api/feedback/bad-case：对话页"没解决"按钮 → 写 bad_cases（PENDING）。
  与转人工自动沉淀共用同一张表（reply 为空区分自动/手动通道）。
- POST /api/feedback/suggestion：对话页"问题没答案"提议 → 写 suggestions（PENDING）。
user_id 取自 JWT（绝不信请求体，沿用 chat 路由约定）。
"""

from fastapi import APIRouter, Depends

from campus_desk.api.deps import (
    AuthUser,
    get_current_user,
    get_owned_conversation,
    get_session_factory,
)
from campus_desk.api.schemas import (
    FeedbackBadCaseRequest,
    FeedbackResponse,
    FeedbackSuggestionRequest,
)
from campus_desk.db.models import BadCase, Suggestion
from campus_desk.db.session import SessionFactory

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("/bad-case", response_model=FeedbackResponse)
def feedback_bad_case(
    payload: FeedbackBadCaseRequest,
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """手动"没解决"反馈：question 必填，reply/note 可选（带回复便于审查判断）。

    M15A-⑦ 归属校验：thread_id 必须是当前用户的会话，否则 404（此前任何人拿到
    会话 ID 都能往反馈表写脏数据）。
    """
    with session_factory() as session, session.begin():
        get_owned_conversation(session, payload.thread_id, user.id)
        row = BadCase(
            user_id=user.id,
            thread_id=payload.thread_id,
            question=payload.question,
            reply=payload.reply,
            note=payload.note,
            status="PENDING",
        )
        session.add(row)
        session.flush()
        rid = row.id
    return FeedbackResponse(id=rid)


@router.post("/suggestion", response_model=FeedbackResponse)
def feedback_suggestion(
    payload: FeedbackSuggestionRequest,
    user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """用户提议：question 必填，note 为补充说明。"""
    with session_factory() as session, session.begin():
        row = Suggestion(
            user_id=user.id,
            question=payload.question,
            note=payload.note,
            status="PENDING",
        )
        session.add(row)
        session.flush()
        rid = row.id
    return FeedbackResponse(id=rid)
