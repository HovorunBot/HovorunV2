"""add_reddit_and_split_provider_commands

Revision ID: f3a4b5c6d7e8
Revises: 2c01ece4472f
Create Date: 2026-10-21 17:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from hovorunv2.infrastructure.persistence.migration_operations import insert_ignore, migration_tables

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "2c01ece4472f"
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
    """Seed split commands and preserve provider activation for existing chats."""
    connection = op.get_bind()
    commands, chats, links = migration_tables(connection, "commands", "chats", "chat_commands")
    connection.execute(links.delete().where(links.c.command_id.in_(sa.select(commands.c.id).where(commands.c.name == "vision"))))
    connection.execute(commands.delete().where(commands.c.name == "vision"))
    for provider in PROVIDERS:
        for suffix, description in (("", "links"), ("_video", "video links"), ("_post", "image/text links")):
            connection.execute(insert_ignore(connection, commands).values(name=provider + suffix, description=f"Process {provider.capitalize()} {description}"))
    rows = sa.select(chats.c.id, commands.c.id).select_from(chats.join(commands, sa.true())).where(chats.c.status == "approved", commands.c.name.in_(("reddit", "reddit_video", "reddit_post")))
    connection.execute(insert_ignore(connection, links).from_select(["chat_id", "command_id"], rows))
    parent = commands.alias("parent")
    child = commands.alias("child")
    rows = sa.select(links.c.chat_id, child.c.id).select_from(links.join(parent, links.c.command_id == parent.c.id).join(child, child.c.name.in_((parent.c.name + "_video", parent.c.name + "_post")))).where(parent.c.name.in_(PROVIDERS))
    connection.execute(insert_ignore(connection, links).from_select(["chat_id", "command_id"], rows))


def downgrade() -> None:
    """Remove split provider commands and reddit command."""
    connection = op.get_bind()
    commands, links = migration_tables(connection, "commands", "chat_commands")
    names = ["reddit", *[f"{provider}_{suffix}" for provider in PROVIDERS for suffix in ("video", "post")]]
    connection.execute(links.delete().where(links.c.command_id.in_(sa.select(commands.c.id).where(commands.c.name.in_(names)))))
    connection.execute(commands.delete().where(commands.c.name.in_(names)))
