"""client phone optional (quick booking without phone)

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-05 14:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.alter_column('phone', existing_type=sa.String(length=32), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.alter_column('phone', existing_type=sa.String(length=32), nullable=False)
