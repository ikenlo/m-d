"""service needs_materials (coloring: order without materials is highlighted)

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-05 16:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('services', schema=None) as batch_op:
        batch_op.add_column(sa.Column('needs_materials', sa.Boolean(), server_default='0', nullable=False))
    # фарбування, мелірування, Total blonde, висвітлення — завжди з матеріалами (директор може змінити).
    # Кирилицю порівнюємо в Python: lower()/LIKE у SQLite не змінюють регістр не-ASCII літер.
    bind = op.get_bind()
    keys = ("фарбуван", "мелір", "total", "висвітл", "блонд")
    rows = bind.execute(sa.text("SELECT id, name, category, subcategory FROM services")).fetchall()
    ids = [r[0] for r in rows if any(k in " ".join(x or "" for x in r[1:]).lower() for k in keys)]
    for i in ids:
        bind.execute(sa.text("UPDATE services SET needs_materials = 1 WHERE id = :i"), {"i": i})

def downgrade() -> None:
    with op.batch_alter_table('services', schema=None) as batch_op:
        batch_op.drop_column('needs_materials')
