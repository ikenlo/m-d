"""Канон 0.3.0: ціна-знімок, ставка майстра, статуси замовлень, version, ключі ідемпотентності

Минулі замовлення не перераховуються (D-2): price_snapshot = amount, master_rate_bp = share_bp,
status = completed. Колонки share_bp / materials_mode у orders лишаються як історія.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-11 13:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None

VERSIONED = ('appointments', 'shifts', 'payouts', 'expenses')


def upgrade() -> None:
    with op.batch_alter_table('users') as b:
        b.alter_column('share_bp', new_column_name='master_rate_bp', existing_type=sa.Integer())
    op.execute("UPDATE users SET master_rate_bp = 5000 WHERE master_rate_bp IS NULL")
    with op.batch_alter_table('users') as b:
        b.alter_column('master_rate_bp', existing_type=sa.Integer(), nullable=False, server_default='5000')

    with op.batch_alter_table('orders') as b:
        b.add_column(sa.Column('price_snapshot', sa.Integer(), nullable=True))
        b.add_column(sa.Column('master_rate_bp', sa.Integer(), server_default='5000', nullable=False))
        b.add_column(sa.Column('status', sa.String(length=16), server_default='completed', nullable=False))
        b.add_column(sa.Column('materials_not_entered', sa.Boolean(), server_default='0', nullable=False))
        b.add_column(sa.Column('version', sa.Integer(), server_default='1', nullable=False))
        b.alter_column('share_bp', existing_type=sa.Integer(), nullable=True)
        b.alter_column('materials_mode', existing_type=sa.String(length=16), nullable=True)
    op.execute("UPDATE orders SET price_snapshot = amount, master_rate_bp = COALESCE(share_bp, 5000)")
    op.execute(
        "UPDATE orders SET materials_not_entered = 1 WHERE service_id IN "
        "(SELECT id FROM services WHERE needs_materials = 1) "
        "AND NOT EXISTS (SELECT 1 FROM order_materials om WHERE om.order_id = orders.id)"
    )
    with op.batch_alter_table('orders') as b:
        b.alter_column('price_snapshot', existing_type=sa.Integer(), nullable=False)
        b.create_index('ix_orders_status', ['status'])

    with op.batch_alter_table('order_materials') as b:
        b.alter_column('unit_cost', new_column_name='unit_price_snapshot', existing_type=sa.Integer())

    for table in VERSIONED:
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column('version', sa.Integer(), server_default='1', nullable=False))
    with op.batch_alter_table('payouts') as b:
        b.add_column(sa.Column('tag', sa.String(length=32), nullable=True))

    op.create_table(
        'idempotency_keys',
        sa.Column('key', sa.String(length=96), nullable=False),
        sa.Column('request_hash', sa.String(length=64), nullable=True),
        sa.Column('response_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_index('ix_idempotency_keys_created_at', 'idempotency_keys', ['created_at'])
    op.execute("DELETE FROM settings WHERE key IN ('default_share_bp', 'default_materials_mode')")


def downgrade() -> None:
    op.drop_index('ix_idempotency_keys_created_at', table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
    with op.batch_alter_table('payouts') as b:
        b.drop_column('tag')
    for table in VERSIONED:
        with op.batch_alter_table(table) as b:
            b.drop_column('version')
    with op.batch_alter_table('order_materials') as b:
        b.alter_column('unit_price_snapshot', new_column_name='unit_cost', existing_type=sa.Integer())
    op.execute("UPDATE orders SET share_bp = COALESCE(share_bp, master_rate_bp), materials_mode = COALESCE(materials_mode, 'salon')")
    with op.batch_alter_table('orders') as b:
        b.drop_index('ix_orders_status')
        b.drop_column('version')
        b.drop_column('materials_not_entered')
        b.drop_column('status')
        b.drop_column('master_rate_bp')
        b.drop_column('price_snapshot')
        b.alter_column('share_bp', existing_type=sa.Integer(), nullable=False)
        b.alter_column('materials_mode', existing_type=sa.String(length=16), nullable=False)
    with op.batch_alter_table('users') as b:
        b.alter_column('master_rate_bp', new_column_name='share_bp', existing_type=sa.Integer(), nullable=True,
                       server_default=None)
