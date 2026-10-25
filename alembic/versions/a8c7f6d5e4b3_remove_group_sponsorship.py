"""Remove ongoing sponsorship; group whitelist approvals remain independent.

Revision ID: a8c7f6d5e4b3
Revises: member019
"""

import json
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "a8c7f6d5e4b3"
down_revision = "member019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop withdrawn sponsor associations without changing approved access or configuration."""
    connection = op.get_bind()
    count = connection.execute(sa.text("SELECT COUNT(*) FROM group_sponsors")).scalar_one()
    removed_evidence = connection.execute(
        sa.text("DELETE FROM member_evidence WHERE admin_candidate = 0 AND needs_verification = 0")
    ).rowcount
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (created_at, actor_origin, target_type, target_id, action, changed_fields) "
            "VALUES (:now, 'migration', 'group_grant', 'sponsorship', 'remove_sponsorship', :changes)"
        ),
        {
            "now": datetime.now(UTC).isoformat(),
            "changes": json.dumps({"removed_relationships": count, "removed_irrelevant_evidence": removed_evidence}),
        },
    )
    op.drop_table("group_sponsors")


def downgrade() -> None:
    """Restore the previous empty schema, never fabricate withdrawn sponsor relationships."""
    op.create_table(
        "group_sponsors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_group_sponsors_group_id", "group_sponsors", ["group_id"])
    op.create_index("ix_group_sponsors_user_id", "group_sponsors", ["user_id"])
    op.create_index(
        "uq_group_sponsors_active",
        "group_sponsors",
        ["group_id", "user_id"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL AND revoked_at IS NULL"),
    )
