"""add_bluesky_command_support

Revision ID: 766879474765
Revises: 8e99124cf63c
Create Date: 2026-04-26 23:23:45.123456

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '766879474765'
down_revision: Union[str, Sequence[str], None] = '8e99124cf63c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable bluesky command for existing whitelisted chats."""
    # 1. Ensure command exists
    op.execute(
        "INSERT OR IGNORE INTO commands (name, description) VALUES ('bluesky', 'Process Bluesky links')"
    )
    
    # 2. Link command to all whitelisted chats
    op.execute(
        """
        INSERT OR IGNORE INTO chat_commands (chat_id, command_id)
        SELECT chats.id, commands.id
        FROM chats, commands
        WHERE chats.is_whitelisted = 1 AND commands.name = 'bluesky'
        """
    )


def downgrade() -> None:
    """Remove bluesky command links from all chats."""
    op.execute(
        """
        DELETE FROM chat_commands
        WHERE command_id IN (SELECT id FROM commands WHERE name = 'bluesky')
        """
    )
