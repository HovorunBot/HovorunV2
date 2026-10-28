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
    chats = sa.table("chats", sa.column("status"), sa.column("is_whitelisted"))
    op.execute(chats.update().values(status=sa.case((chats.c.is_whitelisted.is_(True), "approved"), else_="unauthorized")))

    # 3. Drop old column and make new one non-nullable
    with op.batch_alter_table('chats') as batch_op:
        batch_op.alter_column('status', nullable=False)
        batch_op.drop_column('is_whitelisted')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('chats', sa.Column('is_whitelisted', sa.BOOLEAN(), nullable=True))
    chats = sa.table("chats", sa.column("status"), sa.column("is_whitelisted"))
    op.execute(chats.update().values(is_whitelisted=chats.c.status == "approved"))

    with op.batch_alter_table('chats') as batch_op:
        batch_op.alter_column('is_whitelisted', nullable=False)
        batch_op.drop_column('status')
