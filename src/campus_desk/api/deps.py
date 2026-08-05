"""FastAPI 依赖（M6）：会话工厂/图注册表取用 + JWT 鉴权 + RBAC 角色门控。

鉴权链路：Authorization: Bearer <jwt> → 解 claims（sub=user_id, role）→
AuthUser；require_roles 工厂按角色放行（student 仅自己的数据在路由层过滤）。
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
        return AuthUser(id=claims["sub"], role=claims["role"])
    except (jwt.PyJWTError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="未登录或登录已过期") from exc


def require_roles(*roles: str):
    """RBAC 门控工厂：角色不在白名单 → 403（依赖里先过 get_current_user 得 401）。"""

    def _check(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="无权访问")
        return user

    return _check
