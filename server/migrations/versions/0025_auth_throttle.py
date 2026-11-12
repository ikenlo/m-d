"""auth_throttle: блокування перебору PIN бота (і за IP) у БД

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-11 12:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'auth_throttle',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('fails', sa.Integer(), nullable=False),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scope', 'key', name='uq_auth_throttle_scope_key'),
    )


def downgrade() -> None:
    op.drop_table('auth_throttle')
