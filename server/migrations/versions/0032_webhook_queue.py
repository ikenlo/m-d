"""webhook_queue: черга вхідних вебхуків (Meta) для MDs Manager

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-12 09:30:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'webhook_queue',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('retry_count', sa.Integer(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.Column('processing_started_at', sa.DateTime(), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index('ix_webhook_queue_status', 'webhook_queue', ['status'])
    op.create_index('ix_webhook_queue_received_at', 'webhook_queue', ['received_at'])


def downgrade() -> None:
    op.drop_index('ix_webhook_queue_received_at', table_name='webhook_queue')
    op.drop_index('ix_webhook_queue_status', table_name='webhook_queue')
    op.drop_table('webhook_queue')
