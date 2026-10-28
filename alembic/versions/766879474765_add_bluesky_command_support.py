"""add_bluesky_command_support

Revision ID: 766879474765
Revises: 8e99124cf63c
Create Date: 2026-04-26 23:23:45.123456

"""
from typing import Sequence, Union

from alembic import op
from hovorunv2.infrastructure.persistence.migration_operations import insert_ignore, migration_tables
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '766879474765'
down_revision: Union[str, Sequence[str], None] = '8e99124cf63c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable provider commands for existing whitelisted chats."""
    connection = op.get_bind()
    commands, chats, links = migration_tables(connection, "commands", "chats", "chat_commands")
    names = ('bluesky',)
    for name in names:
        connection.execute(insert_ignore(connection, commands).values(name=name, description=f"Process {name.capitalize()} links"))
    rows = sa.select(chats.c.id, commands.c.id).select_from(chats.join(commands, sa.true())).where(chats.c.is_whitelisted.is_(True), commands.c.name.in_(names))
    connection.execute(insert_ignore(connection, links).from_select(["chat_id", "command_id"], rows))


def downgrade() -> None:
    """Remove provider links without deleting existing command definitions."""
    connection = op.get_bind()
    commands, links = migration_tables(connection, "commands", "chat_commands")
    connection.execute(links.delete().where(links.c.command_id.in_(sa.select(commands.c.id).where(commands.c.name.in_(('bluesky',))))))
