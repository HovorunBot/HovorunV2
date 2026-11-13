"""Add chat settings_version and bounded setup_apply_operations idempotency table.

Revision ID: setup065
Revises: i18n064
"""

import sqlalchemy as sa

from alembic import op

revision = "setup065"
down_revision = "i18n064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add settings_version on chats and setup_apply_operations for atomic setup apply."""
    op.add_column(
        "chats",
        sa.Column("settings_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "setup_apply_operations",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("chat_generation", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("applied_values", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index("ix_setup_apply_operations_chat_id", "setup_apply_operations", ["chat_id"])
    op.create_index("ix_setup_apply_operations_actor_id", "setup_apply_operations", ["actor_id"])
    op.create_index("ix_setup_apply_operations_expires_at", "setup_apply_operations", ["expires_at"])


def downgrade() -> None:
    """Drop setup_apply_operations and chats.settings_version."""
    op.drop_index("ix_setup_apply_operations_expires_at", table_name="setup_apply_operations")
    op.drop_index("ix_setup_apply_operations_actor_id", table_name="setup_apply_operations")
    op.drop_index("ix_setup_apply_operations_chat_id", table_name="setup_apply_operations")
    op.drop_table("setup_apply_operations")
    op.drop_column("chats", "settings_version")
