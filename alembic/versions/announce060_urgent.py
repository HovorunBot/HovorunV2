"""Add explicit urgent confirmation and preference-bypass markers.

Revision ID: announce060
Revises: announce059
"""

import sqlalchemy as sa

from alembic import op

revision = "announce060"
down_revision = "announce059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Mark urgent confirmations and the events allowed to bypass category preferences."""
    op.add_column(
        "announcement_confirmations",
        sa.Column("urgent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("announcement_confirmations", sa.Column("urgent_reason", sa.Text(), nullable=True))
    op.add_column(
        "notification_events",
        sa.Column("bypass_preferences", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Remove only the additive urgent markers."""
    op.drop_column("notification_events", "bypass_preferences")
    op.drop_column("announcement_confirmations", "urgent_reason")
    op.drop_column("announcement_confirmations", "urgent")
