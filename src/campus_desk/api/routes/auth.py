"""登录路由（M6 + M4）：账号（users.id 或学号）+ 密码 → JWT。

M4：登录校验 enabled；JWT 携带最终权限并集（角色默认 ∪ 附加位）；
登录成功写审计日志（旁路，不阻断）；禁用账号登录 403。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select

from campus_desk.api.deps import get_session_factory
from campus_desk.api.schemas import LoginRequest, LoginResponse, UserInfo
from campus_desk.audit import write_audit
from campus_desk.db.models import User
from campus_desk.db.session import SessionFactory
from campus_desk.perms import effective_perms
from campus_desk.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, session_factory: SessionFactory = Depends(get_session_factory)):
    """用户名匹配 users.id 或 student_no（学生两种都能登）；密码错统一 401。"""
    with session_factory() as session:
        user = session.execute(
            select(User).where(
                or_(User.id == payload.username, User.student_no == payload.username)
            )
        ).scalar_one_or_none()
        if (
            user is None
            or user.password_hash is None
            or not verify_password(payload.password, user.password_hash)
        ):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if not user.enabled:
            raise HTTPException(status_code=403, detail="账号已禁用，请联系管理员")
        perms = effective_perms(user.role, user.permissions)
    write_audit(
        session_factory, user_id=user.id, action="login", object_type="system", detail="登录成功"
    )
    token, expires_in = create_access_token(user.id, user.role, perms)
    return LoginResponse(
        token=token,
        expires_in=expires_in,
        user=UserInfo(
            id=user.id,
            name=user.name,
            role=user.role,
            dept=user.dept,
            student_no=user.student_no,
            permissions=perms,
        ),
    )
