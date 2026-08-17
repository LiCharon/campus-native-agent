"""权限位常量（M4，设计 v3 §2）：角色默认权限 + 附加权限位并集。

与前端 constants/perms.js 同源（角色默认位一致）；users.permissions 列存
附加权限位（逗号分隔），最终权限 = 角色默认 ∪ 附加位。
"""

from __future__ import annotations

# 角色默认权限位
ROLE_PERMS: dict[str, list[str]] = {
    "student": ["chat"],
    "cs_staff": ["chat", "cs_workbench"],
    "admin": ["chat", "cs_workbench", "kb_review", "view_stats", "user_mgmt", "view_logs"],
}

# 可被 admin 授予的附加权限位
GRANTABLE_PERMS: list[str] = [
    "cs_workbench",
    "kb_review",
    "view_stats",
    "user_mgmt",
    "view_logs",
]


def effective_perms(role: str, extra: str = "") -> list[str]:
    """最终权限 = 角色默认 ∪ 附加位（extra: 逗号分隔字符串）。"""
    base = ROLE_PERMS.get(role, ["chat"])
    extras = [s.strip() for s in (extra or "").split(",") if s.strip()]
    return list(dict.fromkeys([*base, *extras]))
