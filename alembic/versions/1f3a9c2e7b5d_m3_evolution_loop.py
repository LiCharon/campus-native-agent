"""m3 evolution loop: suggestions table + bad_cases thread_id/note columns

进化闭环（M3，设计 §5.5）：
- suggestions 表：用户提议通道（PENDING/ADOPTED/REJECTED）
- bad_cases 加 thread_id（对话关联，手动反馈通道）/ note（补充说明）

Revision ID: 1f3a9c2e7b5d
Revises: 252f1aa7a5c3
Create Date: 2026-08-16 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f3a9c2e7b5d"
down_revision: str | Sequence[str] | None = "252f1aa7a5c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # note 列不用 server_default：MySQL 8 的 TEXT 列不支持 DEFAULT（1101 错误），
    # 模型层 default="" 由 ORM 插入时提供；列可空兼容存量行（NULL=无补充说明）
    op.add_column("bad_cases", sa.Column("thread_id", sa.String(length=64), nullable=True))
    op.add_column("bad_cases", sa.Column("note", sa.Text(), nullable=True))
    op.create_table(
        "suggestions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=8), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suggestions")),
    )
    op.create_index(op.f("ix_suggestions_status"), "suggestions", ["status"], unique=False)
    op.create_index(op.f("ix_suggestions_user_id"), "suggestions", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_suggestions_user_id"), table_name="suggestions")
    op.drop_index(op.f("ix_suggestions_status"), table_name="suggestions")
    op.drop_table("suggestions")
    op.drop_column("bad_cases", "note")
    op.drop_column("bad_cases", "thread_id")
