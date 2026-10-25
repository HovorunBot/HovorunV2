"""Preserve legacy approval and characterized empty-selection compatibility.

Revision ID: legacy016
Revises: grant015
"""

import json
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "legacy016"
down_revision = "grant015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Backfill only known active legacy groups without changing their configuration."""
    connection = op.get_bind()
    duplicates = connection.execute(
        sa.text(
            "SELECT platform, chat_id FROM chats WHERE deleted_at IS NULL "
            "GROUP BY platform, chat_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicates:
        msg = "Cannot preserve legacy access: duplicate active group identity"
        raise ValueError(msg)
    op.add_column("chats", sa.Column("legacy_empty_capture", sa.Boolean(), nullable=False, server_default=sa.false()))
    groups = (
        connection.execute(
            sa.text(
                "SELECT id, status, NOT EXISTS (SELECT 1 FROM chat_commands cc WHERE cc.chat_id = chats.id) AS empty_commands "
                "FROM chats WHERE deleted_at IS NULL AND chat_type IN ('group', 'supergroup')"
            )
        )
        .mappings()
        .all()
    )
    timestamp = datetime.now(UTC).isoformat()
    for group in groups:
        changed = {}
        if group["empty_commands"]:
            connection.execute(sa.text("UPDATE chats SET legacy_empty_capture = 1 WHERE id = :id"), {"id": group["id"]})
            changed["legacy_empty_capture"] = True
        if group["status"] == "approved":
            connection.execute(
                sa.text("INSERT INTO group_approvals (group_id, basis, created_at) VALUES (:id, 'legacy', :now)"),
                {"id": group["id"], "now": timestamp},
            )
            changed["approval_basis"] = "legacy"
        if changed:
            connection.execute(
                sa.text(
                    "INSERT INTO audit_events (created_at, actor_origin, target_type, target_id, action, changed_fields) "
                    "VALUES (:now, 'migration', 'group_grant', :id, 'preserve_legacy', :changes)"
                ),
                {"now": timestamp, "id": str(group["id"]), "changes": json.dumps(changed)},
            )


def downgrade() -> None:
    """Remove migration-created bases while retaining append-only audit history."""
    op.execute(sa.text("DELETE FROM group_approvals WHERE basis = 'legacy'"))
    op.drop_column("chats", "legacy_empty_capture")
