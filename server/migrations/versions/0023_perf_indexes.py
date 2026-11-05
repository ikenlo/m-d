"""perf: індекси для календаря, звітів, журналу

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-05 22:00:00
"""
from alembic import op


revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None

# (назва, таблиця, колонки) — лише додавання індексів, дані не змінюються
INDEXES = [
    ('ix_appt_starts', 'appointments', ['starts_at']),
    ('ix_appt_client', 'appointments', ['client_id']),
    ('ix_order_closed', 'orders', ['closed_at']),
    ('ix_order_master_closed', 'orders', ['master_id', 'closed_at']),
    ('ix_order_client', 'orders', ['client_id']),
    ('ix_order_shift', 'orders', ['shift_id']),
    ('ix_payout_cash_shift', 'payouts', ['cash_shift_id']),
    ('ix_expense_spent_on', 'expenses', ['spent_on']),
    ('ix_audit_ts', 'audit_log', ['ts']),
    ('ix_audit_entity', 'audit_log', ['entity', 'entity_id']),
]


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _cols in reversed(INDEXES):
        op.drop_index(name, table_name=table)
