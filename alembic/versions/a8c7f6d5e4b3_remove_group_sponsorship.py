"""Remove ongoing sponsorship; group whitelist approvals remain independent.

Revision ID: a8c7f6d5e4b3
Revises: member019
"""

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
    metadata = sa.MetaData()
    sponsors = sa.Table("group_sponsors", metadata, autoload_with=connection)
    evidence = sa.Table("member_evidence", metadata, autoload_with=connection)
    audit = sa.Table("audit_events", metadata, autoload_with=connection)
    count = connection.scalar(sa.select(sa.func.count()).select_from(sponsors))
    removed_evidence = connection.execute(evidence.delete().where(evidence.c.admin_candidate.is_(False), evidence.c.needs_verification.is_(False))).rowcount
    connection.execute(audit.insert().values(created_at=datetime.now(UTC), actor_origin="migration", target_type="group_grant", target_id="sponsorship", action="remove_sponsorship", changed_fields={"removed_relationships": count, "removed_irrelevant_evidence": removed_evidence}))
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
        postgresql_where=sa.text("deleted_at IS NULL AND revoked_at IS NULL"),
    )
