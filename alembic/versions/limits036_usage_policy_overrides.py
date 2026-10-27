"""Add ordinary usage overrides without changing access or existing configuration.

Revision ID: limits036
Revises: groupbeta029
"""

import sqlalchemy as sa

from alembic import op

revision = "limits036"
down_revision = "groupbeta029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create empty lifecycle-bound overrides; defaults remain code-defined."""
    op.create_table(
        "usage_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=True),
        sa.Column("bucket", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("burst", sa.Integer(), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("pending", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("(user_id IS NULL) <> (group_id IS NULL)", name="ck_usage_override_one_subject"),
        sa.CheckConstraint("unit IN ('requests', 'resource_units', 'tokens')", name="ck_usage_override_unit"),
        sa.CheckConstraint("(bucket LIKE 'feature:%' AND length(bucket) > 8) OR (bucket LIKE 'family:%' AND length(bucket) > 7)", name="ck_usage_override_bucket"),
        sa.CheckConstraint('window_seconds > 0 AND "limit" > 0 AND burst > 0 AND burst <= "limit" '
                           'AND concurrency > 0 AND pending > 0', name="ck_usage_override_positive"),
    )
    for subject in ("user_id", "group_id"):
        kind = "user" if subject == "user_id" else "group"
        op.create_index(
            f"uq_usage_override_{kind}_active", "usage_overrides",
            [subject, "bucket", "unit", "window_seconds"], unique=True,
            sqlite_where=sa.text(f"deleted_at IS NULL AND {subject} IS NOT NULL"),
        )


def downgrade() -> None:
    """Remove only the additive policy table."""
    op.drop_table("usage_overrides")
