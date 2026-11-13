"""Видалення застарілих колонок materials_mode та share_bp

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-13 15:40:00

Колонки лишалися після канону 0.3.0 і більше не використовувались.
Видалення згідно з ROADMAP §0.3.3.
"""
import sqlalchemy as sa
from alembic import op


revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users') as b:
        b.drop_column('materials_mode')

    with op.batch_alter_table('orders') as b:
        b.drop_column('materials_mode')
        b.drop_column('share_bp')


def downgrade() -> None:
    with op.batch_alter_table('users') as b:
        b.add_column(sa.Column('materials_mode', sa.String(length=16), nullable=True))

    with op.batch_alter_table('orders') as b:
        b.add_column(sa.Column('materials_mode', sa.String(length=16), nullable=True))
        b.add_column(sa.Column('share_bp', sa.Integer(), nullable=True))
