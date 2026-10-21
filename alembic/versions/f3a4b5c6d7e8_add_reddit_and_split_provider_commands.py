"""add_reddit_and_split_provider_commands

Revision ID: f3a4b5c6d7e8
Revises: 2c01ece4472f
Create Date: 2026-10-21 17:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

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
    """Seed reddit and split video/post commands and backfill existing chats."""
    # 1. Remove unused vision command if present
    op.execute("DELETE FROM chat_commands WHERE command_id IN (SELECT id FROM commands WHERE name = 'vision')")
    op.execute("DELETE FROM commands WHERE name = 'vision'")

    # 2. Ensure all provider commands and split <provider>_video / <provider>_post commands exist
    for provider in PROVIDERS:
        label = provider.capitalize()
        op.execute(
            f"INSERT OR IGNORE INTO commands (name, description) VALUES ('{provider}', 'Process {label} links')"
        )
        op.execute(
            f"INSERT OR IGNORE INTO commands (name, description) "
            f"VALUES ('{provider}_video', 'Process {label} video links')"
        )
        op.execute(
            f"INSERT OR IGNORE INTO commands (name, description) "
            f"VALUES ('{provider}_post', 'Process {label} image/text links')"
        )

    # 3. Enable new reddit provider (and its split commands) for all currently approved chats
    op.execute(
        """
        INSERT OR IGNORE INTO chat_commands (chat_id, command_id)
        SELECT chats.id, commands.id
        FROM chats, commands
        WHERE chats.status = 'approved'
          AND commands.name IN ('reddit', 'reddit_video', 'reddit_post')
        """
    )

    # 4. Backfill <provider>_video and <provider>_post for all existing chats that have <provider> enabled
    op.execute(
        """
        INSERT OR IGNORE INTO chat_commands (chat_id, command_id)
        SELECT cc.chat_id, sub_cmd.id
        FROM chat_commands cc
        JOIN commands parent_cmd ON cc.command_id = parent_cmd.id
        JOIN commands sub_cmd ON sub_cmd.name IN (parent_cmd.name || '_video', parent_cmd.name || '_post')
        WHERE parent_cmd.name IN (
            'twitter', 'instagram', 'tiktok', 'threads', 'bluesky', 'facebook', 'youtube', 'reddit'
        )
        """
    )


def downgrade() -> None:
    """Remove split provider commands and reddit command."""
    split_and_reddit = [
        "reddit",
        *[f"{p}_{suffix}" for p in PROVIDERS for suffix in ("video", "post")],
    ]
    names_sql = ", ".join(f"'{name}'" for name in split_and_reddit)
    op.execute(
        f"""
        DELETE FROM chat_commands
        WHERE command_id IN (SELECT id FROM commands WHERE name IN ({names_sql}))
        """
    )
    op.execute(f"DELETE FROM commands WHERE name IN ({names_sql})")
