"""FastAPI 依赖（M6 + M4）：会话工厂/图注册表取用 + JWT 鉴权 + RBAC 角色/权限门控。

鉴权链路：Authorization: Bearer <jwt> → 解 claims（sub=user_id, role, perms）→
AuthUser；require_roles 工厂按角色放行；require_perm 按权限位放行
（claims 里的 perms 是登录时算好的"角色默认 ∪ 附加位"并集，改权限需重登）。
"""

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select

from campus_desk.api.graphs import GraphRegistry
from campus_desk.db.models import Conversation
from campus_desk.db.session import SessionFactory
from campus_desk.security import decode_access_token


@dataclass(frozen=True)
class AuthUser:
    """JWT 解析结果（鉴权不查库——claims 登录时已校验存在性）。"""

    id: str
    role: str
    perms: tuple[str, ...] = ()


def get_session_factory(request: Request) -> SessionFactory:
    return request.app.state.session_factory


def get_registry(request: Request) -> GraphRegistry:
    return request.app.state.registry


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return authorization[len("Bearer ") :]


def get_current_user(authorization: str | None = Header(None)) -> AuthUser:
    """解 JWT；无效/过期/缺失 → 401（无 token 与坏 token 同 401）。"""
    try:
        claims = decode_access_token(_bearer_token(authorization))
        return AuthUser(
            id=claims["sub"],
            role=claims["role"],
            perms=tuple(claims.get("perms") or []),
        )
    except (jwt.PyJWTError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="未登录或登录已过期") from exc


def find_owned_conversation(session, thread_id: str, user_id: str) -> Conversation | None:
    """按 thread_id + user_id 取归属会话，查不到返回 None（不抛）。

    用于"有则处理、无则跳过"的容错分支（如落 assistant 消息时会话可能已被并发删除）。
    """
    return (
        session.execute(
            select(Conversation).where(
                Conversation.thread_id == thread_id,
                Conversation.user_id == user_id,
            )
        )
        .scalars()
        .first()
    )


def get_owned_conversation(session, thread_id: str, user_id: str) -> Conversation:
    """归属校验（M15A-⑦）：查不到或不属于该用户 → 404。

    chat 与 feedback 共用同一口径——此前 feedback 完全没校验、chat 是内联查询，
    两份逻辑各自漂移风险高（归属口径变了只改一处即可）。

    一律 404、不用 403：区分"不存在"与"存在但越权"等于泄露"该 thread 存在"。

    调用方须在已有事务内调用（chat 需要"校验 + 落 user 消息"同事务）。
    """
    conv = find_owned_conversation(session, thread_id, user_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


def require_roles(*roles: str):
    """RBAC 角色门控工厂：角色不在白名单 → 403。"""

    def _check(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="无权访问")
        return user

    return _check


def require_perm(*perms: str):
    """权限位门控工厂（M4）：最终权限并集不含任一权限位 → 403。

    角色默认权限由 perms.py 在登录时算好进 JWT claims，这里只查 claims。
    """

    def _check(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if not any(p in user.perms for p in perms):
            raise HTTPException(status_code=403, detail="无权访问")
        return user

    return _check
