"""client base: card fields (birthday, second phone, source, wishes, consent…) and search_name

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-05 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phone2', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('birthday', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('telegram_username', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('wishes', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('marketing_consent', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('last_contact_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('search_name', sa.String(length=160), nullable=True))
        batch_op.create_index(batch_op.f('ix_clients_search_name'), ['search_name'], unique=False)
    # пошуковий ключ для наявних клієнтів (нижній регістр кирилиці — у Python)
    bind = op.get_bind()
    for cid, name in bind.execute(sa.text("SELECT id, name FROM clients")).fetchall():
        bind.execute(sa.text("UPDATE clients SET search_name = :n WHERE id = :i"),
                     {"n": " ".join((name or "").lower().split()) or None, "i": cid})


def downgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clients_search_name'))
        for c in ('search_name', 'last_contact_at', 'marketing_consent', 'wishes', 'source', 'telegram_username', 'birthday', 'phone2'):
            batch_op.drop_column(c)
