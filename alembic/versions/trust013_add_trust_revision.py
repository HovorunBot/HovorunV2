"""Add durable trust reconciliation revision.

Revision ID: trust013
Revises: d4e5f6a7b8c9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "trust013"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve existing trust while adding an initial policy revision."""
    op.add_column("users", sa.Column("trust_revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    """Remove the reconciliation marker without changing trust."""
    op.drop_column("users", "trust_revision")
