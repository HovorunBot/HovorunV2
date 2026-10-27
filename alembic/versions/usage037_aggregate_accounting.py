"""Add stable-subject aggregate usage and replay receipts, without queued payloads.

Revision ID: usage037
Revises: limits036
"""

import sqlalchemy as sa

from alembic import op

revision = "usage037"
down_revision = "limits036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create empty nondeletable accounting independent of entity generations."""
    op.create_table(
        "usage_aggregates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("bucket", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("platform", "subject_kind", "telegram_id", "bucket", "unit", "window_seconds", "window_start",
                            name="uq_usage_aggregate_window"),
        sa.CheckConstraint("platform = 'telegram'", name="ck_usage_aggregate_platform"),
        sa.CheckConstraint("(subject_kind = 'user' AND telegram_id > 0) OR (subject_kind = 'group' AND telegram_id < 0)",
                           name="ck_usage_aggregate_subject"),
        sa.CheckConstraint("unit IN ('requests', 'resource_units', 'tokens')", name="ck_usage_aggregate_unit"),
        sa.CheckConstraint("window_seconds > 0 AND window_start >= 0 AND window_start % window_seconds = 0 AND amount > 0",
                           name="ck_usage_aggregate_positive"),
    )
    op.create_table(
        "usage_accounting_events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(event_id) BETWEEN 1 AND 128", name="ck_usage_accounting_event_id"),
        sa.CheckConstraint("length(fingerprint) = 64", name="ck_usage_accounting_fingerprint"),
    )


def downgrade() -> None:
    """Remove only accounting tables; this explicitly discards their accounting history."""
    op.drop_table("usage_accounting_events")
    op.drop_table("usage_aggregates")
