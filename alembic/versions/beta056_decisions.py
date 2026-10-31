"""Add beta decision storage and inline markup on notification events.

Revision ID: beta056
Revises: notif054
"""

import sqlalchemy as sa

from alembic import op

revision = "beta056"
down_revision = "notif054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create decisions and add optional inline markup for announcement messages."""
    op.create_table(
        "beta_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=True),
        sa.Column("feature", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND group_id IS NULL) OR (user_id IS NULL AND group_id IS NOT NULL)",
            name="ck_beta_decisions_one_subject",
        ),
        sa.CheckConstraint("decision IN ('active', 'declined')", name="ck_beta_decisions_decision"),
        sa.CheckConstraint("length(feature) > 0", name="ck_beta_decisions_feature"),
    )
    for subject in ("user_id", "group_id"):
        kind = "user" if subject == "user_id" else "group"
        op.create_index(
            f"uq_beta_decisions_{kind}_active",
            "beta_decisions",
            [subject, "feature"],
            unique=True,
            sqlite_where=sa.text(f"deleted_at IS NULL AND {subject} IS NOT NULL"),
            postgresql_where=sa.text(f"deleted_at IS NULL AND {subject} IS NOT NULL"),
        )
    op.add_column("notification_events", sa.Column("body_markup", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove only the additive decisions and inline markup."""
    op.drop_column("notification_events", "body_markup")
    op.drop_table("beta_decisions")
