"""m6: users.password_hash（登录鉴权，M6）

Revision ID: d6e41f2a9b80
Revises: c62b33f98479
Create Date: 2026-08-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e41f2a9b80"
down_revision: str | Sequence[str] | None = "c62b33f98479"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # nullable：存量行不阻塞迁移；哈希回填由种子负责（demo 语义，不走迁移）
    op.add_column("users", sa.Column("password_hash", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
