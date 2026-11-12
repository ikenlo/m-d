"""Канон 0.3.0: журнал входів (90 днів), PIN вкладки «Фінанси», архівування витрат

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-11 17:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'login_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(length=64), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('method', sa.String(length=16), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('detail', sa.String(length=128), nullable=True),
        sa.Column('ts', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_login_events_ts', 'login_events', ['ts'])
    op.create_index('ix_login_events_user_id', 'login_events', ['user_id'])
    with op.batch_alter_table('users') as b:
        b.add_column(sa.Column('finance_pin_hash', sa.String(length=255), nullable=True))
    with op.batch_alter_table('expenses') as b:
        b.add_column(sa.Column('archived_at', sa.DateTime(), nullable=True))
    op.execute("UPDATE expenses SET archived_at = created_at WHERE is_deleted = 1")


def downgrade() -> None:
    with op.batch_alter_table('expenses') as b:
        b.drop_column('archived_at')
    with op.batch_alter_table('users') as b:
        b.drop_column('finance_pin_hash')
    op.drop_index('ix_login_events_user_id', table_name='login_events')
    op.drop_index('ix_login_events_ts', table_name='login_events')
    op.drop_table('login_events')
