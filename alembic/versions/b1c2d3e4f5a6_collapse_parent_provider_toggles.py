"""collapse_parent_provider_toggles

Revision ID: b1c2d3e4f5a6
Revises: f3a4b5c6d7e8
Create Date: 2026-10-23 23:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

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
    """Expand legacy parent rows into split rows, then remove parent rows."""
    # 1. Expand parent rows to split rows for any chat that had parent enabled
    for provider in PROVIDERS:
        op.execute(
            f"""
            INSERT OR IGNORE INTO chat_commands (chat_id, command_id)
            SELECT cc.chat_id, sub_cmd.id
            FROM chat_commands cc
            JOIN commands parent_cmd ON cc.command_id = parent_cmd.id
            JOIN commands sub_cmd ON sub_cmd.name IN ('{provider}_video', '{provider}_post')
            WHERE parent_cmd.name = '{provider}'
            """
        )

    names_sql = ", ".join(f"'{name}'" for name in PROVIDERS)

    # 2. Delete parent provider rows from chat_commands
    op.execute(
        f"""
        DELETE FROM chat_commands
        WHERE command_id IN (SELECT id FROM commands WHERE name IN ({names_sql}))
        """
    )

    # 3. Delete parent provider rows from commands
    op.execute(f"DELETE FROM commands WHERE name IN ({names_sql})")


def downgrade() -> None:
    """Recreate parent provider commands and re-insert chat associations."""
    # 1. Recreate parent commands
    for provider in PROVIDERS:
        label = provider.capitalize()
        op.execute(
            f"INSERT OR IGNORE INTO commands (name, description) VALUES ('{provider}', 'Process {label} links')"
        )

    # 2. Re-populate parent rows where any split row exists
    for provider in PROVIDERS:
        op.execute(
            f"""
            INSERT OR IGNORE INTO chat_commands (chat_id, command_id)
            SELECT DISTINCT cc.chat_id, (SELECT id FROM commands WHERE name = '{provider}')
            FROM chat_commands cc
            JOIN commands sub_cmd ON cc.command_id = sub_cmd.id
            WHERE sub_cmd.name IN ('{provider}_video', '{provider}_post')
            """
        )
