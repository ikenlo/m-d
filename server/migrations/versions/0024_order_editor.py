"""orders: хто і коли востаннє редагував замовлення

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-05 12:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('orders') as b:
        b.add_column(sa.Column('edited_by', sa.Integer(), nullable=True))
        b.add_column(sa.Column('edited_at', sa.DateTime(), nullable=True))
        b.create_foreign_key('fk_orders_edited_by_users', 'users', ['edited_by'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('orders') as b:
        b.drop_constraint('fk_orders_edited_by_users', type_='foreignkey')
        b.drop_column('edited_at')
        b.drop_column('edited_by')
