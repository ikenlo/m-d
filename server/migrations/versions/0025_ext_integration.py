"""інтеграція з MDs manager: Instagram ID клієнта, атрибуція записів

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-05 22:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('clients') as b:
        b.add_column(sa.Column('instagram_user_id', sa.String(length=64), nullable=True))
        b.create_unique_constraint('uq_clients_instagram_user_id', ['instagram_user_id'])
    op.create_table(
        'appointment_attribution',
        sa.Column('appointment_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('lead_source', sa.String(length=64), nullable=True),
        sa.Column('utm_source', sa.String(length=64), nullable=True),
        sa.Column('utm_medium', sa.String(length=64), nullable=True),
        sa.Column('utm_campaign', sa.String(length=128), nullable=True),
        sa.Column('utm_content', sa.String(length=128), nullable=True),
        sa.Column('campaign_id', sa.String(length=64), nullable=True),
        sa.Column('external_ref', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['appointment_id'], ['appointments.id']),
        sa.PrimaryKeyConstraint('appointment_id'),
        sa.UniqueConstraint('external_ref'),
    )
    op.create_index('ix_appointment_attribution_lead_source', 'appointment_attribution', ['lead_source'])


def downgrade() -> None:
    op.drop_index('ix_appointment_attribution_lead_source', table_name='appointment_attribution')
    op.drop_table('appointment_attribution')
    with op.batch_alter_table('clients') as b:
        b.drop_constraint('uq_clients_instagram_user_id', type_='unique')
        b.drop_column('instagram_user_id')
