"""Канон 0.3.0: товари, бонус майстра 10%, єдиний журнал руху складу, націнка

- products, order_products, bonus_accruals;
- stock_moves: item_type, product_id, reason (канонічна причина), unit_price (ціна на момент руху);
- materials.markup_bp;
- orders.appointment_id / service_id / client_id можуть бути порожніми (продаж товару без послуги).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-11 15:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None

REASONS = {"receipt": "receipt", "order": "consumption", "order_return": "return", "adjust": "inventory",
           "writeoff": "correction"}


def upgrade() -> None:
    op.create_table(
        'products',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('sku', sa.String(length=64), nullable=True),
        sa.Column('barcode', sa.String(length=64), nullable=True),
        sa.Column('category', sa.String(length=64), nullable=True),
        sa.Column('cost_price_cents', sa.Integer(), nullable=False),
        sa.Column('sale_price_cents', sa.Integer(), nullable=False),
        sa.Column('markup_bp', sa.Integer(), server_default='10000', nullable=False),
        sa.Column('stock_qty', sa.Integer(), server_default='0', nullable=False),
        sa.Column('active', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sku'),
        sa.UniqueConstraint('barcode'),
    )
    op.create_table(
        'order_products',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.Column('sale_price_snapshot_cents', sa.Integer(), nullable=False),
        sa.Column('cost_price_snapshot_cents', sa.Integer(), nullable=False),
        sa.Column('master_bonus_cents', sa.Integer(), nullable=False),
        sa.Column('refunded', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name='fk_order_products_order_id'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], name='fk_order_products_product_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_order_products_order_id', 'order_products', ['order_id'])
    op.create_table(
        'bonus_accruals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('master_id', sa.Integer(), nullable=False),
        sa.Column('order_product_id', sa.Integer(), nullable=True),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('accrued_on', sa.Date(), nullable=False),
        sa.Column('payout_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['master_id'], ['users.id'], name='fk_bonus_accruals_master_id'),
        sa.ForeignKeyConstraint(['order_product_id'], ['order_products.id'], name='fk_bonus_accruals_op_id'),
        sa.ForeignKeyConstraint(['payout_id'], ['payouts.id'], name='fk_bonus_accruals_payout_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_bonus_master_unpaid', 'bonus_accruals', ['master_id', 'payout_id'])

    with op.batch_alter_table('materials') as b:
        b.add_column(sa.Column('markup_bp', sa.Integer(), server_default='10000', nullable=False))
    with op.batch_alter_table('stock_moves') as b:
        b.add_column(sa.Column('item_type', sa.String(length=16), server_default='material', nullable=False))
        b.add_column(sa.Column('product_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('reason', sa.String(length=16), nullable=True))
        b.add_column(sa.Column('unit_price', sa.Integer(), nullable=True))
        b.alter_column('material_id', existing_type=sa.Integer(), nullable=True)
        b.create_foreign_key('fk_stock_moves_product_id', 'products', ['product_id'], ['id'])
    for kind, reason in REASONS.items():
        op.execute(sa.text("UPDATE stock_moves SET reason = :r WHERE kind = :k").bindparams(r=reason, k=kind))
    op.execute("UPDATE stock_moves SET unit_price = (SELECT unit_cost FROM materials m WHERE m.id = stock_moves.material_id)")

    with op.batch_alter_table('orders') as b:
        b.alter_column('appointment_id', existing_type=sa.Integer(), nullable=True)
        b.alter_column('service_id', existing_type=sa.Integer(), nullable=True)
        b.alter_column('client_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('stock_moves') as b:
        b.drop_constraint('fk_stock_moves_product_id', type_='foreignkey')
        b.drop_column('unit_price')
        b.drop_column('reason')
        b.drop_column('product_id')
        b.drop_column('item_type')
    with op.batch_alter_table('materials') as b:
        b.drop_column('markup_bp')
    op.drop_index('ix_bonus_master_unpaid', table_name='bonus_accruals')
    op.drop_table('bonus_accruals')
    op.drop_index('ix_order_products_order_id', table_name='order_products')
    op.drop_table('order_products')
    op.drop_table('products')
