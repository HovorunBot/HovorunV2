"""extend_chat_identity_and_foreign_keys

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-24 21:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preflight check duplicate chats, add metadata columns, and enforce active uniqueness."""
    conn = op.get_bind()

    # Preflight check for duplicate existing numeric chat records
    chats = sa.table("chats", sa.column("platform"), sa.column("chat_id"))
    duplicates = conn.execute(sa.select(chats.c.platform, chats.c.chat_id, sa.func.count()).group_by(chats.c.platform, chats.c.chat_id).having(sa.func.count() > 1)).fetchall()
    if duplicates:
        dup_summary = ", ".join(f"{row[0]}:{row[1]} (count: {row[2]})" for row in duplicates)
        msg = f"Migration aborted: duplicate active chats detected: {dup_summary}. Manual resolution required."
        raise ValueError(msg)

    with op.batch_alter_table("chats") as batch_op:
        batch_op.add_column(sa.Column("title", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("chat_type", sa.String(length=32), nullable=True, server_default="group"))
        batch_op.add_column(sa.Column("is_bot_present", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("bot_capabilities_verified_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("generation", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deleted_by_actor_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("deleted_reason", sa.String(), nullable=True))
        batch_op.create_index("ix_chats_chat_id", ["chat_id"], unique=False)
        batch_op.create_index(
            "uq_chats_active_identity",
            ["platform", "chat_id"],
            unique=True,
            sqlite_where=sa.text("deleted_at IS NULL"),
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    """Revert chat metadata columns and indexes."""
    with op.batch_alter_table("chats") as batch_op:
        batch_op.drop_index("uq_chats_active_identity")
        batch_op.drop_index("ix_chats_chat_id")
        batch_op.drop_column("deleted_reason")
        batch_op.drop_column("deleted_by_actor_id")
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("generation")
        batch_op.drop_column("bot_capabilities_verified_at")
        batch_op.drop_column("is_bot_present")
        batch_op.drop_column("chat_type")
        batch_op.drop_column("title")
