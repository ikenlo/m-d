"""appointment close reminder flag

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-05 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('close_reminded', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.drop_column('close_reminded')
