"""m10 knowledge dense vector column

Revision ID: b1c2d3e4f5a6
Revises: a5656bea1a25
Create Date: 2026-08-21

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a5656bea1a25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # knowledge_entries 加稠密向量列（bge-small-zh 512 维，JSON 存 Text）
    op.add_column(
        "knowledge_entries",
        sa.Column("dense_vector", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("knowledge_entries", "dense_vector")
