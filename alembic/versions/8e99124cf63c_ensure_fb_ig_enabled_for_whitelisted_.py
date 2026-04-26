"""ensure_fb_ig_enabled_for_whitelisted_chats

Revision ID: 8e99124cf63c
Revises: 49a52f144f4f
Create Date: 2026-04-26 23:09:41.282121

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e99124cf63c'
down_revision: Union[str, Sequence[str], None] = '49a52f144f4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable facebook and instagram commands for existing whitelisted chats."""
    # 1. Ensure commands exist
    op.execute(
        "INSERT OR IGNORE INTO commands (name, description) VALUES ('facebook', 'Process Facebook links')"
    )
    op.execute(
        "INSERT OR IGNORE INTO commands (name, description) VALUES ('instagram', 'Process Instagram links')"
    )
    
    # 2. Link commands to all whitelisted chats
    op.execute(
        """
        INSERT OR IGNORE INTO chat_commands (chat_id, command_id)
        SELECT chats.id, commands.id
        FROM chats, commands
        WHERE chats.is_whitelisted = 1 AND commands.name IN ('facebook', 'instagram')
        """
    )


def downgrade() -> None:
    """Remove facebook and instagram command links from all chats."""
    op.execute(
        """
        DELETE FROM chat_commands
        WHERE command_id IN (SELECT id FROM commands WHERE name IN ('facebook', 'instagram'))
        """
    )
