"""Add one default-disabled speech mode without changing media selections.

Revision ID: speech063
Revises: chatcmd062
"""

import sqlalchemy as sa

from alembic import op

revision = "speech063"
down_revision = "chatcmd062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Default every existing chat to Off and reject unsupported mode states."""
    op.add_column(
        "chats",
        sa.Column(
            "speech_mode",
            sa.Enum(
                "off",
                "manual",
                "manual_and_automatic",
                native_enum=False,
                create_constraint=True,
                name="speech_mode_values",
            ),
            nullable=False,
            server_default="off",
        ),
    )


def downgrade() -> None:
    """Remove only the speech mode field and its local constraint."""
    op.drop_column("chats", "speech_mode")
