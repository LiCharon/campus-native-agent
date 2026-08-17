"""FastAPI 依赖（M6 + M4）：会话工厂/图注册表取用 + JWT 鉴权 + RBAC 角色/权限门控。

鉴权链路：Authorization: Bearer <jwt> → 解 claims（sub=user_id, role, perms）→
AuthUser；require_roles 工厂按角色放行；require_perm 按权限位放行
（claims 里的 perms 是登录时算好的"角色默认 ∪ 附加位"并集，改权限需重登）。
"""

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, Request

from campus_desk.api.graphs import GraphRegistry
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
