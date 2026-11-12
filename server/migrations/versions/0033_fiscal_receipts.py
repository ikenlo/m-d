"""Checkbox (ПРРО): orders.fiscal_receipt_id і черга фіскальних чеків fiscal_receipts

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-12 16:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('orders') as b:
        b.add_column(sa.Column('fiscal_receipt_id', sa.String(length=36), nullable=True))
    op.create_table(
        'fiscal_receipts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('payment_type', sa.String(length=16), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('related_uuid', sa.String(length=36), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(length=255), nullable=True),
        sa.Column('fiscal_code', sa.String(length=64), nullable=True),
        sa.Column('fiscal_date', sa.String(length=40), nullable=True),
        sa.Column('tax_url', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('done_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name='fk_fiscal_receipts_order_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
        sa.UniqueConstraint('order_id', 'kind', name='uq_fiscal_receipt_order_kind'),
    )
    op.create_index('ix_fiscal_receipt_status', 'fiscal_receipts', ['status'])
    op.create_index('ix_fiscal_receipt_order', 'fiscal_receipts', ['order_id'])


def downgrade() -> None:
    op.drop_index('ix_fiscal_receipt_order', table_name='fiscal_receipts')
    op.drop_index('ix_fiscal_receipt_status', table_name='fiscal_receipts')
    op.drop_table('fiscal_receipts')
    with op.batch_alter_table('orders') as b:
        b.drop_column('fiscal_receipt_id')
