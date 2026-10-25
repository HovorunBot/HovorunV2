"""Store only policy-relevant membership and capability evidence.

Revision ID: member019
Revises: legacy016
"""

import sqlalchemy as sa

from alembic import op

revision = "member019"
down_revision = "legacy016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create evidence state without backfilling guessed members or freshness."""
    op.create_table(
        "group_membership_states",
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), primary_key=True),
        sa.Column("policy_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("admins_known", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("admins_verified_at", sa.DateTime(timezone=True)),
        sa.Column("admins_expires_at", sa.DateTime(timezone=True)),
        sa.Column("bot_status", sa.String(7), nullable=False, server_default="unknown"),
        sa.Column("bot_telegram_id", sa.Integer()),
        sa.Column("bot_verified_at", sa.DateTime(timezone=True)),
        sa.Column("bot_expires_at", sa.DateTime(timezone=True)),
        sa.Column("can_delete_messages", sa.Boolean()),
        sa.Column("can_restrict_members", sa.Boolean()),
        sa.Column("can_invite_users", sa.Boolean()),
        sa.Column("can_pin_messages", sa.Boolean()),
        sa.Column("can_manage_chat", sa.Boolean()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("bot_status IN ('unknown', 'member', 'admin', 'left')", name="ck_bot_membership_status"),
    )
    op.create_table(
        "member_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(7), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admin_candidate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("needs_verification", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('unknown', 'member', 'admin', 'left')", name="ck_member_evidence_status"),
    )
    op.create_index("ix_member_evidence_group_id", "member_evidence", ["group_id"])
    op.create_index("ix_member_evidence_user_id", "member_evidence", ["user_id"])
    op.create_index(
        "uq_member_evidence_active",
        "member_evidence",
        ["group_id", "user_id"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Drop evidence only; durable access grants are unaffected."""
    op.drop_table("member_evidence")
    op.drop_table("group_membership_states")
