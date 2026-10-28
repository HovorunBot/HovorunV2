"""Add independent group approvals, sponsorships and access requests.

Revision ID: grant015
Revises: ban014
"""

import sqlalchemy as sa

from alembic import op

revision = "grant015"
down_revision = "ban014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create generation-bound grants with retained lifecycle history."""
    for table in ("group_approvals", "group_sponsors", "group_access_requests"):
        columns = [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        ]
        constraints = []
        if table == "group_approvals":
            columns.extend(
                [
                    sa.Column("basis", sa.String(8), nullable=False),
                    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
                ]
            )
            constraints.append(sa.CheckConstraint("basis IN ('explicit', 'legacy')", name="approvalbasis"))
        elif table == "group_sponsors":
            columns.extend(
                [
                    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
                ]
            )
        else:
            columns.extend(
                [
                    sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("status", sa.String(9), nullable=False, server_default="pending"),
                    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
                    sa.Column("decision_actor_id", sa.Integer(), nullable=True),
                    sa.Column("decision_reason", sa.String(), nullable=True),
                ]
            )
            constraints.append(
                sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'withdrawn')", name="requeststatus")
            )
        op.create_table(table, *columns, *constraints)
        op.create_index(f"ix_{table}_group_id", table, ["group_id"])
    op.create_index("ix_group_sponsors_user_id", "group_sponsors", ["user_id"])
    op.create_index(
        "uq_group_approvals_active",
        "group_approvals",
        ["group_id", "basis"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL AND revoked_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_index(
        "uq_group_sponsors_active",
        "group_sponsors",
        ["group_id", "user_id"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL AND revoked_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_index(
        "uq_group_requests_pending",
        "group_access_requests",
        ["group_id"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL AND status = 'pending'"),
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'pending'"),
    )


def downgrade() -> None:
    """Remove this additive schema only."""
    for table in ("group_access_requests", "group_sponsors", "group_approvals"):
        op.drop_table(table)
