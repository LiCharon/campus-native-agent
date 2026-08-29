"""m13 llm usage metering

Revision ID: c3f8a1b27d94
Revises: b1c2d3e4f5a6
Create Date: 2026-08-29

M13-ZJUT 成本检测：建 llm_usage 表，记录每次 LLM 调用的 token 三件套 +
调用点/模型/归属（user_id/thread_id/route）。**不落 estimated_cost**——单价可变，
费用一律由 scripts/cost_report.py 按当前 config 单价派生。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8a1b27d94"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("route", sa.String(length=16), nullable=True),
        sa.Column("call_point", sa.String(length=16), nullable=True),
        # model 留 64：deepseek-v4-flash-vision-exp 27 字符，换更长模型名仍有余量
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("llm_usage", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_llm_usage_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_llm_usage_thread_id"), ["thread_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_llm_usage_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # 先删索引再删表（MySQL：drop_table 带索引易触发 1553 外键/索引序问题，显式删更安全）
    with op.batch_alter_table("llm_usage", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_llm_usage_user_id"))
        batch_op.drop_index(batch_op.f("ix_llm_usage_thread_id"))
        batch_op.drop_index(batch_op.f("ix_llm_usage_created_at"))
    op.drop_table("llm_usage")
