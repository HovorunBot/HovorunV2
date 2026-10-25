"""add_role_column_to_users

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-24 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add role column to users defaulting to 'user' and backfill from historical user_roles."""
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=32), nullable=False, server_default="user"),
    )

    # Backfill assignments from historical user_roles table:
    # OWNER takes precedence over ADMIN; no assignment means 'user'.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE users SET role = 'owner' "
            "WHERE id IN (SELECT user_id FROM user_roles WHERE role = 'owner')"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE users SET role = 'admin' "
            "WHERE id IN (SELECT user_id FROM user_roles WHERE role = 'admin') AND role != 'owner'"
        )
    )


def downgrade() -> None:
    """Drop role column from users table."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("role")
