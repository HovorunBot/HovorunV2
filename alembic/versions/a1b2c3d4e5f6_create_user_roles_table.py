"""create_user_roles_table

Revision ID: a1b2c3d4e5f6
Revises: f5a6b7c8d9e0
Create Date: 2026-10-24 22:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create user_roles table and indexes. Starts completely empty (no seeded IDs)."""
    op.create_table(
        "user_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="telegram"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_roles_user_id", "user_roles", ["user_id"], unique=False)
    op.create_index("ix_user_roles_telegram_id", "user_roles", ["telegram_id"], unique=False)
    op.create_index(
        "uq_user_roles_assignment",
        "user_roles",
        ["platform", "user_id", "role"],
        unique=True,
    )


def downgrade() -> None:
    """Drop user_roles table and indexes."""
    op.drop_index("uq_user_roles_assignment", table_name="user_roles")
    op.drop_index("ix_user_roles_telegram_id", table_name="user_roles")
    op.drop_index("ix_user_roles_user_id", table_name="user_roles")
    op.drop_table("user_roles")
