"""antispam: client blocked for online booking

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-05 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('blocked', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('blocked_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_column('blocked_reason')
        batch_op.drop_column('blocked')
