"""Add lifecycle-bound beta entitlements without changing selected features.

Revision ID: beta028
Revises: group025_routing_aliases
"""

import sqlalchemy as sa

from alembic import op

revision = "beta028"
down_revision = "group025_routing_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create empty grants; never infer eligibility from trust, role or existing selections."""
    op.create_table(
        "beta_entitlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=True),
        sa.Column("feature", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND group_id IS NULL) OR (user_id IS NULL AND group_id IS NOT NULL)",
            name="ck_beta_entitlements_one_subject",
        ),
        sa.CheckConstraint("group_id IS NULL OR feature <> '*'", name="ck_beta_entitlements_group_feature"),
        sa.CheckConstraint("length(feature) > 0", name="ck_beta_entitlements_feature"),
    )
    op.create_index(
        "uq_beta_entitlements_user_active", "beta_entitlements", ["user_id", "feature"], unique=True,
        sqlite_where=sa.text("revoked_at IS NULL AND deleted_at IS NULL AND user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_beta_entitlements_group_active", "beta_entitlements", ["group_id", "feature"], unique=True,
        sqlite_where=sa.text("revoked_at IS NULL AND deleted_at IS NULL AND group_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove only the additive entitlement table."""
    op.drop_index("uq_beta_entitlements_group_active", table_name="beta_entitlements")
    op.drop_index("uq_beta_entitlements_user_active", table_name="beta_entitlements")
    op.drop_table("beta_entitlements")
