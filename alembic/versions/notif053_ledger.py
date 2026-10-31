"""Track immutable notification events and per-recipient delivery outcomes.

Revision ID: notif053
Revises: notif052
"""

import sqlalchemy as sa

from alembic import op

revision = "notif053"
down_revision = "notif052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the event ledger and per-recipient receipts; no message contents are stored."""
    op.create_table(
        "notification_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("introduction", sa.String(96), nullable=False),
        sa.Column("release_identity", sa.String(96), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_key", name="uq_notification_event_key"),
    )
    op.create_table(
        "notification_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("notification_events.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (group_id IS NULL)", name="ck_notification_receipt_one_subject"
        ),
        sa.UniqueConstraint("event_id", "user_id", name="uq_notification_receipt_event_user"),
        sa.UniqueConstraint("event_id", "group_id", name="uq_notification_receipt_event_group"),
    )
    op.create_index("ix_notification_receipts_status", "notification_receipts", ["status"])


def downgrade() -> None:
    """Remove only the additive notification ledger."""
    op.drop_index("ix_notification_receipts_status", table_name="notification_receipts")
    op.drop_table("notification_receipts")
    op.drop_table("notification_events")
