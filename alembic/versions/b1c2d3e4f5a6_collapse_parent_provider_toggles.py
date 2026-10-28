"""collapse_parent_provider_toggles

Revision ID: b1c2d3e4f5a6
Revises: f3a4b5c6d7e8
Create Date: 2026-10-23 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from hovorunv2.infrastructure.persistence.migration_operations import insert_ignore, migration_tables

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDERS: tuple[str, ...] = (
    "twitter",
    "instagram",
    "tiktok",
    "threads",
    "bluesky",
    "facebook",
    "youtube",
    "reddit",
)


def upgrade() -> None:
    """Expand legacy parents into split rows, then remove parent rows."""
    connection = op.get_bind()
    commands, links = migration_tables(connection, "commands", "chat_commands")
    parent = commands.alias("parent")
    child = commands.alias("child")
    for provider in PROVIDERS:
        rows = sa.select(links.c.chat_id, child.c.id).select_from(links.join(parent, links.c.command_id == parent.c.id).join(child, child.c.name.in_((provider + "_video", provider + "_post")))).where(parent.c.name == provider)
        connection.execute(insert_ignore(connection, links).from_select(["chat_id", "command_id"], rows))
    connection.execute(links.delete().where(links.c.command_id.in_(sa.select(commands.c.id).where(commands.c.name.in_(PROVIDERS)))))
    connection.execute(commands.delete().where(commands.c.name.in_(PROVIDERS)))


def downgrade() -> None:
    """Recreate parent commands and links for chats with selected split rows."""
    connection = op.get_bind()
    commands, links = migration_tables(connection, "commands", "chat_commands")
    for provider in PROVIDERS:
        connection.execute(insert_ignore(connection, commands).values(name=provider, description=f"Process {provider.capitalize()} links"))
        parent_id = sa.select(commands.c.id).where(commands.c.name == provider).scalar_subquery()
        rows = sa.select(links.c.chat_id, parent_id).select_from(links.join(commands, links.c.command_id == commands.c.id)).where(commands.c.name.in_((provider + "_video", provider + "_post"))).distinct()
        connection.execute(insert_ignore(connection, links).from_select(["chat_id", "command_id"], rows))
