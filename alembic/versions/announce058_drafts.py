"""Add encrypted announcement drafts and their selected internal targets.

Revision ID: announce058
Revises: beta056
"""

import sqlalchemy as sa

from alembic import op

revision = "announce058"
down_revision = "beta056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the draft and target tables with safe constraints."""
    op.create_table(
        "announcement_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("audience", sa.String(16), nullable=False),
        sa.Column("body_ciphertext", sa.Text(), nullable=False),
        sa.Column("body_salt", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("preview_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('release', 'beta', 'feature_status', 'announcement')",
            name="ck_announcement_drafts_category",
        ),
        sa.CheckConstraint(
            "audience IN ('all', 'users', 'groups', 'selected')",
            name="ck_announcement_drafts_audience",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_announcement_drafts_revision"),
        sa.CheckConstraint("length(body_ciphertext) > 0", name="ck_announcement_drafts_body"),
    )
    op.create_table(
        "announcement_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("announcement_drafts.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("chats.id"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND group_id IS NULL) OR (user_id IS NULL AND group_id IS NOT NULL)",
            name="ck_announcement_targets_one_subject",
        ),
    )
    for subject, kind in (("user_id", "user"), ("group_id", "group")):
        op.create_index(
            f"uq_announcement_targets_{kind}_active",
            "announcement_targets",
            ["draft_id", subject],
            unique=True,
            sqlite_where=sa.text(f"deleted_at IS NULL AND {subject} IS NOT NULL"),
            postgresql_where=sa.text(f"deleted_at IS NULL AND {subject} IS NOT NULL"),
        )


def downgrade() -> None:
    """Remove only the announcement draft tables."""
    op.drop_table("announcement_targets")
    op.drop_table("announcement_drafts")
