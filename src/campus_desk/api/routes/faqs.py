"""FAQ 路由（M7）：读 = 登录即可（student/staff/admin），写 = 仅 admin。

路由风格照抄 tickets.py：APIRouter(prefix="/api") + Depends(get_current_user)/
require_roles 工厂；列表按 id 排序。读路径走 faq_cache 热点缓存（Redis 未配/
连不上自动降级直查），任何写操作成功后 flush_faqs() 失效缓存——保证管理页
改完即见（cache-aside 一致性）。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from campus_desk import faq_cache
from campus_desk.api.deps import AuthUser, get_current_user, get_session_factory, require_roles
from campus_desk.api.schemas import FaqCreate, FaqListResponse, FaqSummary
from campus_desk.db.models import Faq
from campus_desk.db.session import SessionFactory

router = APIRouter(prefix="/api", tags=["faqs"])

_ADMIN = ("admin",)


def _summary(faq: Faq) -> FaqSummary:
    return FaqSummary(
        id=faq.id,
        category=faq.category,
        keywords=faq.keywords,
        question=faq.question,
        answer=faq.answer,
    )


@router.get("/faqs", response_model=FaqListResponse)
def list_faqs(
    _user: AuthUser = Depends(get_current_user),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """FAQ 列表（登录即可读，含 student/staff；按 id 排序，走 Redis 热点缓存）。"""
    faqs = faq_cache.get_all_faqs(session_factory)
    return FaqListResponse(items=[_summary(f) for f in faqs], total=len(faqs))


@router.post("/admin/faqs", response_model=FaqSummary)
def create_faq(
    payload: FaqCreate,
    _user: AuthUser = Depends(require_roles(*_ADMIN)),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """新增 FAQ（仅 admin）。"""
    with session_factory() as session, session.begin():
        faq = Faq(
            category=payload.category.strip(),
            keywords=payload.keywords.strip(),
            question=payload.question.strip(),
            answer=payload.answer.strip(),
        )
        session.add(faq)
        session.flush()  # 取自增 id（响应需要）
    faq_cache.flush_faqs()
    return _summary(faq)


@router.put("/admin/faqs/{faq_id}", response_model=FaqSummary)
def update_faq(
    faq_id: int,
    payload: FaqCreate,
    _user: AuthUser = Depends(require_roles(*_ADMIN)),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """编辑 FAQ（仅 admin；全量更新）。"""
    with session_factory() as session, session.begin():
        faq = session.execute(select(Faq).where(Faq.id == faq_id)).scalar_one_or_none()
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ 不存在")
        faq.category = payload.category.strip()
        faq.keywords = payload.keywords.strip()
        faq.question = payload.question.strip()
        faq.answer = payload.answer.strip()
    faq_cache.flush_faqs()
    return _summary(faq)


@router.delete("/admin/faqs/{faq_id}")
def delete_faq(
    faq_id: int,
    _user: AuthUser = Depends(require_roles(*_ADMIN)),
    session_factory: SessionFactory = Depends(get_session_factory),
):
    """删除 FAQ（仅 admin）。"""
    with session_factory() as session, session.begin():
        faq = session.execute(select(Faq).where(Faq.id == faq_id)).scalar_one_or_none()
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ 不存在")
        session.delete(faq)
    faq_cache.flush_faqs()
    return {"ok": True}
