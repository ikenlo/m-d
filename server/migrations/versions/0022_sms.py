"""sms: campaigns and message history

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-05 20:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('sms_campaigns',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('audience', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('recipients', sa.Integer(), nullable=False),
    sa.Column('cost', sa.Integer(), nullable=False),
    sa.Column('expense_id', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sms_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=True),
    sa.Column('campaign_id', sa.Integer(), nullable=True),
    sa.Column('phone', sa.String(length=32), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=32), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('provider_id', sa.String(length=64), nullable=True),
    sa.Column('error', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['campaign_id'], ['sms_campaigns.id'], name='fk_sms_messages_campaign_id'),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name='fk_sms_messages_client_id', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sms_messages', schema=None) as batch_op:
        batch_op.create_index('ix_sms_status_created', ['status', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_sms_messages_campaign_id'), ['campaign_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sms_messages_client_id'), ['client_id'], unique=False)


def downgrade() -> None:
    op.drop_table('sms_messages')
    op.drop_table('sms_campaigns')
