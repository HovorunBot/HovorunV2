"""replace_is_whitelisted_with_status

Revision ID: e190225ee4ef
Revises: a5097e6fe12d
Create Date: 2026-05-16 00:35:06.100177

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e190225ee4ef'
down_revision: Union[str, Sequence[str], None] = 'a5097e6fe12d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add column as nullable
    op.add_column('chats', sa.Column('status', sa.String(), nullable=True))

    # 2. Migrate data
    op.execute("UPDATE chats SET status = 'approved' WHERE is_whitelisted = 1")
    op.execute("UPDATE chats SET status = 'unauthorized' WHERE is_whitelisted = 0")
    op.execute("UPDATE chats SET status = 'unauthorized' WHERE status IS NULL")

    # 3. Drop old column and make new one non-nullable
    with op.batch_alter_table('chats') as batch_op:
        batch_op.alter_column('status', nullable=False)
        batch_op.drop_column('is_whitelisted')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('chats', sa.Column('is_whitelisted', sa.BOOLEAN(), nullable=True))
    op.execute("UPDATE chats SET is_whitelisted = 1 WHERE status = 'approved'")
    op.execute("UPDATE chats SET is_whitelisted = 0 WHERE status != 'approved'")

    with op.batch_alter_table('chats') as batch_op:
        batch_op.alter_column('is_whitelisted', nullable=False)
        batch_op.drop_column('status')
