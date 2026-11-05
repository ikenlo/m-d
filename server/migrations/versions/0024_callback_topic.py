"""callback_requests: тема заявки і джерело (швидка заявка з сайту)

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-05 23:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('callback_requests', schema=None) as batch_op:
        batch_op.add_column(sa.Column('topic', sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=16), server_default='telegram', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('callback_requests', schema=None) as batch_op:
        batch_op.drop_column('source')
        batch_op.drop_column('topic')
