"""Preserve historical Telegram group routing without copying identities.

Revision ID: group025_routing_aliases
Revises: a8c7f6d5e4b3
"""

import sqlalchemy as sa

from alembic import op

revision = "group025_routing_aliases"
down_revision = "a8c7f6d5e4b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add routing history; existing group IDs and configuration remain untouched."""
    op.create_table(
        "group_routes",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=False),
    )
    op.create_index("ix_group_routes_group_id", "group_routes", ["group_id"])


def downgrade() -> None:
    """Remove routing history without rewriting canonical group records."""
    op.drop_index("ix_group_routes_group_id", table_name="group_routes")
    op.drop_table("group_routes")
