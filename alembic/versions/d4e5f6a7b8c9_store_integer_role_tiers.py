"""Store ordered integer authority tiers without rewriting role history.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Convert current authority to integer tiers, retaining every identity and historical row."""
    op.add_column("users", sa.Column("role_tier", sa.Integer(), nullable=True))
    users = sa.table("users", sa.column("role"), sa.column("role_tier"))
    op.execute(users.update().values(role_tier=sa.case({"user": 1, "admin": 10, "owner": 100}, value=users.c.role)))
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_valid_role", type_="check")
        batch_op.drop_column("role")
        batch_op.alter_column("role_tier", new_column_name="role", nullable=False, server_default="1")
        batch_op.create_check_constraint("ck_users_valid_role", "role IN (1, 10, 100)")


def downgrade() -> None:
    """Restore textual authority without deriving it from outdated historical assignments."""
    op.add_column("users", sa.Column("role_name", sa.String(length=32), nullable=True))
    users = sa.table("users", sa.column("role"), sa.column("role_name"))
    op.execute(users.update().values(role_name=sa.case({1: "user", 10: "admin", 100: "owner"}, value=users.c.role)))
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_valid_role", type_="check")
        batch_op.drop_column("role")
        batch_op.alter_column("role_name", new_column_name="role", nullable=False, server_default="user")
        batch_op.create_check_constraint("ck_users_valid_role", "role IN ('user', 'admin', 'owner')")
