"""Enforce the single authority role on migrated user tables.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Reject invalid existing roles before adding the ORM's authority constraint."""
    connection = op.get_bind()
    invalid_role = connection.execute(
        sa.text("SELECT 1 FROM users WHERE role NOT IN ('user', 'admin', 'owner') LIMIT 1")
    ).first()
    if invalid_role is not None:
        msg = "Migration aborted: invalid authority roles exist. Resolve them explicitly before upgrading."
        raise ValueError(msg)

    with op.batch_alter_table("users") as batch_op:
        batch_op.create_check_constraint("ck_users_valid_role", "role IN ('user', 'admin', 'owner')")


def downgrade() -> None:
    """Remove only the authority constraint, retaining roles and user history."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_valid_role", type_="check")
