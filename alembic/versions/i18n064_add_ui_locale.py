"""Add nullable UI locale preferences to users and chats tables.

Revision ID: i18n064
Revises: speech063
"""

import sqlalchemy as sa

from alembic import op

revision = "i18n064"
down_revision = "speech063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable UI locale preferences to users and chats tables."""
    op.add_column("users", sa.Column("ui_locale", sa.String(length=16), nullable=True))
    op.add_column("chats", sa.Column("ui_locale", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Drop UI locale preference columns."""
    op.drop_column("chats", "ui_locale")
    op.drop_column("users", "ui_locale")
