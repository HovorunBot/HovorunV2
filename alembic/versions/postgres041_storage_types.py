"""Align PostgreSQL storage types after the preserved 0.2.1 and 0.3 migration chain.

Revision ID: postgres041
Revises: usage037
"""

import sqlalchemy as sa
from alembic import op

revision = "postgres041"
down_revision = "usage037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve Telegram's 64-bit identifiers and decode the legacy serialized language list."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column("chats", "chat_id", type_=sa.BigInteger(), existing_nullable=False)
    op.alter_column("chats", "ignored_langs", type_=sa.JSON(), postgresql_using="ignored_langs::json")
    op.alter_column("group_membership_states", "bot_telegram_id", type_=sa.BigInteger(), existing_nullable=True)
    op.alter_column("audit_events", "actor_id", type_=sa.BigInteger(), existing_nullable=True)
    op.alter_column("group_access_requests", "decision_actor_id", type_=sa.BigInteger(), existing_nullable=True)


def downgrade() -> None:
    """Restore the legacy language representation without narrowing Telegram identities."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column("chats", "ignored_langs", type_=sa.String(), postgresql_using="ignored_langs::text")
