"""登录路由（M6 + M4 + M15A）：账号（users.id 或学号）+ 密码 → JWT。

M4：登录校验 enabled；JWT 携带最终权限并集（角色默认 ∪ 附加位）；
登录成功写审计日志（旁路，不阻断）；禁用账号登录 403。
M15A-③：连续密码失败锁定（按 users.id 计数，锁定与密码错同文案 401，只审记锁定事件）；
M15A-⑨：登录成功且存储串迭代次数低于当前值时，重哈希回写（老密码无感升级）。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select

from campus_desk import rate_limit
from campus_desk.api.deps import get_session_factory
from campus_desk.api.schemas import LoginRequest, LoginResponse, UserInfo
from campus_desk.audit import write_audit
from campus_desk.db.models import User
from campus_desk.db.session import SessionFactory
from campus_desk.perms import effective_perms_from_db
from campus_desk.security import (
    create_access_token,
    hash_password,
    needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 锁定响应必须与"密码错误"完全一致：返回"账号已锁定"等于告诉攻击者
# "这个账号存在"（用户枚举），且泄露防御状态。锁定事实只写进审计。
_UNAUTHORIZED = "用户名或密码错误"


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, session_factory: SessionFactory = Depends(get_session_factory)):
    """用户名匹配 users.id 或 student_no（学生两种都能登）；密码错/锁定统一 401。"""
    just_locked = False
    auth_failed = False
    with session_factory() as session:
        user = session.execute(
            select(User).where(
                or_(User.id == payload.username, User.student_no == payload.username)
            )
        ).scalar_one_or_none()
        # 账号不存在：不计数（护栏 1——不存在的用户名不进内存 dict）
        if user is None or user.password_hash is None:
            raise HTTPException(status_code=401, detail=_UNAUTHORIZED)
        if rate_limit.is_locked(user.id):
            raise HTTPException(status_code=401, detail=_UNAUTHORIZED)

        if not verify_password(payload.password, user.password_hash):
            just_locked = rate_limit.register_failure(user.id)
            auth_failed = True
        elif not user.enabled:
            # 密码正确但禁用：403，且不算密码失败（不计入锁定）
            raise HTTPException(status_code=403, detail="账号已禁用，请联系管理员")
        else:
            rate_limit.register_success(user.id)
            # M15A-⑨ rehash-on-login：历史低迭代哈希在本次登录升级，无需数据迁移
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(payload.password)
                session.commit()
            perms = effective_perms_from_db(session, user.role, user.permissions)

    if auth_failed:
        # 审计只记锁定事件（每次失败都写会把账号锁变成写库放大器）
        if just_locked:
            write_audit(
                session_factory,
                user_id=user.id,
                action="login_locked",
                object_type="user",
                object_id=user.id,
                detail=(
                    f"连续失败 {rate_limit.MAX_FAILS} 次，"
                    f"锁定 {rate_limit.LOCK_SECONDS // 60} 分钟"
                ),
            )
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)

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
