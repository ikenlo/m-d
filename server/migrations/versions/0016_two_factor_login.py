"""two factor login: codes in Telegram and trusted devices

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-04 20:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('login_challenges',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('used', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_login_challenges_user_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('login_challenges', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_login_challenges_user_id'), ['user_id'], unique=False)

    op.create_table('trusted_devices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=160), nullable=True),
    sa.Column('token_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_trusted_devices_user_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('trusted_devices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trusted_devices_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('trusted_devices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_trusted_devices_user_id'))
    op.drop_table('trusted_devices')
    with op.batch_alter_table('login_challenges', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_login_challenges_user_id'))
    op.drop_table('login_challenges')
