"""Add an encrypted body reference to notification events.

Revision ID: notif054
Revises: notif053
"""

import sqlalchemy as sa

from alembic import op

revision = "notif054"
down_revision = "notif053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a nullable opaque payload reference; the body itself stays encrypted in Valkey."""
    op.add_column("notification_events", sa.Column("body_ref", sa.String(32), nullable=True))


def downgrade() -> None:
    """Remove only the additive body reference column."""
    op.drop_column("notification_events", "body_ref")
