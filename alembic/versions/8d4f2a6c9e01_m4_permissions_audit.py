"""m4: users permissions/enabled columns + audit_logs table

权限位（M4，设计 v3 §2）：users.permissions 存附加权限位（逗号分隔），
角色默认权限由代码计算；enabled 禁用登录；audit_logs 审计日志。

Revision ID: 8d4f2a6c9e01
Revises: 1f3a9c2e7b5d
Create Date: 2026-08-17 00:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d4f2a6c9e01"
down_revision: str | Sequence[str] | None = "1f3a9c2e7b5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users", sa.Column("permissions", sa.String(length=128), nullable=False, server_default="")
    )
    op.add_column(
        "users", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1"))
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("object_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("detail", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_user_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_column("users", "enabled")
    op.drop_column("users", "permissions")
