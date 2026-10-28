"""Add explicit group beta selections and supporting eligible administrators.

Revision ID: groupbeta029
Revises: beta028
"""

import sqlalchemy as sa

from alembic import op

revision = "groupbeta029"
down_revision = "beta028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Start with no selected beta features or implied administrator support."""
    op.create_table(
        "group_beta_features",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=False),
        sa.Column("feature", sa.String(128), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(feature) > 0", name="ck_group_beta_features_feature"),
    )
    op.create_index(
        "uq_group_beta_features_active", "group_beta_features", ["group_id", "feature"], unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "group_beta_enablers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("selection_id", sa.Integer(), sa.ForeignKey("group_beta_features.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_group_beta_enablers_active", "group_beta_enablers", ["selection_id", "user_id"], unique=True,
        sqlite_where=sa.text("revoked_at IS NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("revoked_at IS NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Remove only beta-selection support; grants, approval and standard settings remain intact."""
    op.drop_index("uq_group_beta_enablers_active", table_name="group_beta_enablers")
    op.drop_table("group_beta_enablers")
    op.drop_index("uq_group_beta_features_active", table_name="group_beta_features")
    op.drop_table("group_beta_features")
