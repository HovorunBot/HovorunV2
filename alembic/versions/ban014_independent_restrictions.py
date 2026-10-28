"""Add independent stable-subject restrictions.

Revision ID: ban014
Revises: trust013
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ban014"
down_revision: str | Sequence[str] | None = "trust013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add empty restriction history without changing any existing permission."""
    op.create_table(
        "bans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("internal_reason", sa.String(), nullable=False),
        sa.Column("public_explanation", sa.String(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("target_kind IN ('user', 'group')", name="ck_bans_target"),
        sa.CheckConstraint(
            "kind IN ('personal', 'full') AND (target_kind = 'user' OR kind = 'full')", name="ck_bans_kind"
        ),
    )
    op.create_index(
        "uq_bans_active",
        "bans",
        ["platform", "target_kind", "telegram_id", "kind"],
        unique=True,
        sqlite_where=sa.text("revoked_at IS NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("revoked_at IS NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Remove restriction storage only."""
    op.drop_table("bans")
