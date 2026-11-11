"""Канон 0.3.0: сповіщення (рівні, дзвіночок, «оброблено» для обох директорів), запити дозволу (режим відпустки)

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-11 16:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('notifications') as b:
        b.alter_column('chat_id', existing_type=sa.BigInteger(), nullable=True)
        b.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('kind', sa.String(length=32), nullable=True))
        b.add_column(sa.Column('level', sa.String(length=12), nullable=True))
        b.add_column(sa.Column('group_key', sa.String(length=64), nullable=True))
        b.add_column(sa.Column('dedupe_key', sa.String(length=96), nullable=True))
        b.add_column(sa.Column('digest_pending', sa.Boolean(), server_default='0', nullable=False))
        b.add_column(sa.Column('read_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('handled_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('handled_by', sa.Integer(), nullable=True))
        b.create_index('ix_notif_user', ['user_id', 'read_at'])
        b.create_index('ix_notifications_group_key', ['group_key'])
        b.create_unique_constraint('uq_notif_dedupe_user', ['dedupe_key', 'user_id'])
    op.create_table(
        'permission_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=32), nullable=False),
        sa.Column('payload_json', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_by', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('result_json', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_permission_requests_user_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_permission_requests_user_id', 'permission_requests', ['user_id'])
    op.create_index('ix_permission_requests_status', 'permission_requests', ['status'])


def downgrade() -> None:
    op.drop_index('ix_permission_requests_status', table_name='permission_requests')
    op.drop_index('ix_permission_requests_user_id', table_name='permission_requests')
    op.drop_table('permission_requests')
    with op.batch_alter_table('notifications') as b:
        b.drop_constraint('uq_notif_dedupe_user', type_='unique')
        b.drop_index('ix_notifications_group_key')
        b.drop_index('ix_notif_user')
        for c in ('handled_by', 'handled_at', 'read_at', 'digest_pending', 'dedupe_key', 'group_key', 'level', 'kind', 'user_id'):
            b.drop_column(c)
