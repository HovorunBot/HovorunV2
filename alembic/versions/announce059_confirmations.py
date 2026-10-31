"""Add single-use announcement confirmations bound to the draft revision.

Revision ID: announce059
Revises: announce058
"""

import sqlalchemy as sa

from alembic import op

revision = "announce059"
down_revision = "announce058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the confirmation table without touching existing drafts."""
    op.create_table(
        "announcement_confirmations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("announcement_drafts.id"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(64), unique=True, nullable=False),
        sa.Column("audience_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched', 'cancelled', 'expired')",
            name="ck_announcement_confirmations_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_announcement_confirmations_revision"),
    )


def downgrade() -> None:
    """Remove only the confirmation table."""
    op.drop_table("announcement_confirmations")
