"""Узгодження після PR #80: прибрати api_keys, гарантувати auth_throttle

PR #80 (main 0.2.1) мав власну ревізію '0025' (api_keys) з тим самим номером, що й 0025_auth_throttle.
База, оновлена до того main, стоїть на '0025' без auth_throttle — тут таблиця створюється, а api_keys
видаляється (ключі API замінено read-only /api/ext/v1, канон D-5). Для «чистих» баз — нічого не робить.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-12 03:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if 'auth_throttle' not in tables:
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
    if 'api_keys' in tables:
        op.drop_table('api_keys')


def downgrade() -> None:
    pass
