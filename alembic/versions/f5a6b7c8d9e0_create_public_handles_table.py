"""create_public_handles_table

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-10-24 22:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5a6b7c8d9e0"
down_revision: str | Sequence[str] | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create public_handles table and unique indexes."""
    op.create_table(
        "public_handles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("handle_normalized", sa.String(length=64), nullable=False),
        sa.Column("handle_display", sa.String(length=64), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="telegram"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_public_handles_handle",
        "public_handles",
        ["platform", "handle_normalized"],
        unique=True,
    )
    op.create_index(
        "uq_public_handles_target",
        "public_handles",
        ["platform", "target_kind", "target_id"],
        unique=True,
    )


def downgrade() -> None:
    """Drop public_handles table and indexes."""
    op.drop_index("uq_public_handles_target", table_name="public_handles")
    op.drop_index("uq_public_handles_handle", table_name="public_handles")
    op.drop_table("public_handles")
