"""Preserve legacy approval and characterized empty-selection compatibility.

Revision ID: legacy016
Revises: grant015
"""

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from hovorunv2.infrastructure.persistence.migration_operations import migration_tables

revision = "legacy016"
down_revision = "grant015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve only known active legacy groups without changing configuration."""
    connection = op.get_bind()
    chats, links, approvals, audit = migration_tables(connection, "chats", "chat_commands", "group_approvals", "audit_events")
    duplicates = connection.execute(sa.select(chats.c.platform, chats.c.chat_id).where(chats.c.deleted_at.is_(None)).group_by(chats.c.platform, chats.c.chat_id).having(sa.func.count() > 1)).all()
    if duplicates:
        raise ValueError("Cannot preserve legacy access: duplicate active group identity")
    op.add_column("chats", sa.Column("legacy_empty_capture", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("chats", sa.Column("features_configured", sa.Boolean(), nullable=False, server_default=sa.false()))
    chats, = migration_tables(connection, "chats")
    has_commands = sa.select(links.c.chat_id).where(links.c.chat_id == chats.c.id).exists()
    groups = connection.execute(sa.select(chats.c.id, chats.c.status, (~has_commands).label("empty_commands")).where(chats.c.deleted_at.is_(None), chats.c.chat_type.in_(("group", "supergroup")))).mappings().all()
    timestamp = datetime.now(UTC)
    for group in groups:
        changed = {}
        if group["empty_commands"]:
            connection.execute(chats.update().where(chats.c.id == group["id"]).values(legacy_empty_capture=True))
            changed["legacy_empty_capture"] = True
        if group["status"] == "approved":
            connection.execute(approvals.insert().values(group_id=group["id"], basis="legacy", created_at=timestamp))
            changed["approval_basis"] = "legacy"
        if changed:
            connection.execute(audit.insert().values(created_at=timestamp, actor_origin="migration", target_type="group_grant", target_id=str(group["id"]), action="preserve_legacy", changed_fields=changed))
    connection.execute(chats.update().where(sa.or_(chats.c.legacy_empty_capture.is_(True), has_commands)).values(features_configured=True))


def downgrade() -> None:
    """Remove migration-created bases while retaining append-only audit history."""
    approvals = sa.table("group_approvals", sa.column("basis"))
    op.execute(approvals.delete().where(approvals.c.basis == "legacy"))
    op.drop_column("chats", "features_configured")
    op.drop_column("chats", "legacy_empty_capture")
