"""Persist authoritative per-feature emergency state and incrementing execution epoch.

Revision ID: emergency049
Revises: postgres041
"""

import sqlalchemy as sa

from alembic import op

revision = "emergency049"
down_revision = "postgres041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create empty authoritative state; absence means enabled at epoch zero."""
    op.create_table(
        "feature_emergency_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("feature_key", sa.String(96), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column("epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("feature_key", name="uq_feature_emergency_key"),
        sa.CheckConstraint("epoch >= 0", name="ck_feature_emergency_epoch"),
    )


def downgrade() -> None:
    """Remove only the additive emergency-state table."""
    op.drop_table("feature_emergency_states")
