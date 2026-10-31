"""Add soft-deletion markers to command/chat associations.

Revision ID: chatcmd062
Revises: announce060
"""

import sqlalchemy as sa

from alembic import op

revision = "chatcmd062"
down_revision = "announce060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep selection rows for history while normal relationships exclude deleted rows."""
    op.add_column("chat_commands", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove only the soft-deletion marker."""
    with op.batch_alter_table("chat_commands") as batch:
        batch.drop_column("deleted_at")
