"""Add the public-safe outage reason separate from internal emergency notes.

Revision ID: emergency051
Revises: emergency049
"""

import sqlalchemy as sa

from alembic import op

revision = "emergency051"
down_revision = "emergency049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a nullable public-safe reason; historical rows keep an empty value."""
    op.add_column("feature_emergency_states", sa.Column("public_reason", sa.String(512), nullable=True))


def downgrade() -> None:
    """Remove only the additive public reason column."""
    op.drop_column("feature_emergency_states", "public_reason")
