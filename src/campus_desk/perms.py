"""权限位定义与查询（M4 设计 v3 §2 + M6 RBAC 三表化）。

M6 起运行时以 DB 为准：get_role_perms / effective_perms_from_db 查
roles/permissions/role_permissions 三表；下方 ROLE_PERMS / GRANTABLE_PERMS
降级为"种子数据源 + 兜底"（与 db/seed.py 三表种子同源、与前端 constants/perms.js
同源；未知角色/未种子库回退用）。users.permissions 列存附加权限位（逗号分隔），
最终权限 = 角色默认(查库) ∪ 附加位。
"""

from __future__ import annotations

from sqlalchemy import select

from campus_desk.db.models import Permission, RolePermission

# 角色默认权限位（M6 降级为兜底/种子源，运行时查 role_permissions 表）
ROLE_PERMS: dict[str, list[str]] = {
    "student": ["chat"],
    "cs_staff": ["chat", "cs_workbench"],
    "admin": ["chat", "cs_workbench", "kb_review", "view_stats", "user_mgmt", "view_logs"],
}

# 可被 admin 授予的附加权限位（M6 降级为兜底/种子源，运行时查 permissions 表）
GRANTABLE_PERMS: list[str] = [
    "cs_workbench",
    "kb_review",
    "view_stats",
    "user_mgmt",
    "view_logs",
]


def effective_perms(role: str, extra: str = "") -> list[str]:
    """最终权限 = 角色默认 ∪ 附加位（纯函数兜底，extra: 逗号分隔字符串）。"""
    base = ROLE_PERMS.get(role, ["chat"])
    extras = [s.strip() for s in (extra or "").split(",") if s.strip()]
    return list(dict.fromkeys([*base, *extras]))


def get_role_perms(session, role: str) -> list[str]:
    """查库取角色默认权限（role_permissions join permissions，按权限 id 排序）。

    角色在 roles 表无任何关联行（含未知角色）→ 回退 ROLE_PERMS 兜底，保证不锁死。
    """
    rows = (
        session.execute(
            select(Permission.id)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role)
            .order_by(Permission.id)
        )
        .scalars()
        .all()
    )
    return list(rows) or ROLE_PERMS.get(role, ["chat"])


def effective_perms_from_db(session, role: str, extra: str = "") -> list[str]:
    """最终权限 = 角色默认(查库) ∪ 附加位（login 时调用，结果写 JWT claims）。"""
    base = get_role_perms(session, role)
    extras = [s.strip() for s in (extra or "").split(",") if s.strip()]
    return list(dict.fromkeys([*base, *extras]))
