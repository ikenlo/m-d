"""reminders: згода клієнта і позначки надісланих нагадувань

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-04 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reminders', sa.Boolean(), nullable=True))
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reminded_day', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('reminded_2h', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.drop_column('reminded_2h')
        batch_op.drop_column('reminded_day')
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_column('reminders')
