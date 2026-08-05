"""登录路由（M6）：账号（users.id 或学号）+ 密码 → JWT。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select

from campus_desk.api.deps import get_session_factory
from campus_desk.api.schemas import LoginRequest, LoginResponse, UserInfo
from campus_desk.db.models import User
from campus_desk.db.session import SessionFactory
from campus_desk.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, session_factory: SessionFactory = Depends(get_session_factory)):
    """用户名匹配 users.id 或 student_no（学生两种都能登）；密码错统一 401。"""
    with session_factory() as session:
        user = session.execute(
            select(User).where(or_(User.id == payload.username, User.student_no == payload.username))
        ).scalar_one_or_none()
        if user is None or user.password_hash is None or not verify_password(
            payload.password, user.password_hash
        ):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, expires_in = create_access_token(user.id, user.role)
    return LoginResponse(
        token=token,
        expires_in=expires_in,
        user=UserInfo(
            id=user.id,
            name=user.name,
            role=user.role,
            dept=user.dept,
            student_no=user.student_no,
        ),
    )
