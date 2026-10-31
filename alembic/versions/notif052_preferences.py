"""Add category-specific notification preferences with enabled defaults.

Revision ID: notif052
Revises: emergency051
"""

import sqlalchemy as sa

from alembic import op

revision = "notif052"
down_revision = "emergency051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create empty per-subject overrides; absence means the enabled default."""
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (group_id IS NULL)", name="ck_notification_preference_one_subject"
        ),
        sa.CheckConstraint(
            "category IN ('release', 'beta', 'feature_status', 'announcement')",
            name="ck_notification_preference_category",
        ),
    )
    for subject in ("user_id", "group_id"):
        kind = "user" if subject == "user_id" else "group"
        op.create_index(
            f"uq_notification_preference_{kind}_active",
            "notification_preferences",
            [subject, "category"],
            unique=True,
            sqlite_where=sa.text(f"deleted_at IS NULL AND {subject} IS NOT NULL"),
            postgresql_where=sa.text(f"deleted_at IS NULL AND {subject} IS NOT NULL"),
        )


def downgrade() -> None:
    """Remove only the additive preference table."""
    op.drop_table("notification_preferences")
