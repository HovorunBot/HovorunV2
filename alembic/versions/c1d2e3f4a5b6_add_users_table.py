"""add_users_table

Revision ID: c1d2e3f4a5b6
Revises: f3a4b5c6d7e8
Create Date: 2026-10-21 23:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create users table for permission tiers."""
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False, server_default="telegram"),
        sa.Column("tier", sa.Integer(), nullable=False, server_default="10"),
        sa.UniqueConstraint("user_id", "platform", name="uq_user_platform"),
    )
    op.create_index("ix_users_user_id", "users", ["user_id"], unique=False)


def downgrade() -> None:
    """Drop users table."""
    op.drop_index("ix_users_user_id", table_name="users")
    op.drop_table("users")
