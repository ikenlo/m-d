"""Канон 0.3.0: роль «адміністратор» → «директор» (D-4); прибрано налаштування розподілу комунальних

Кожна зміна ролі записується в audit_log. Таблиця master_charges лишається (лише читання) до 0.3.1 (Q-1).

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-11 14:00:00
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None

LEGACY_KEYS = ("utilities_charge_mode", "utilities_charge_share_bp", "utilities_split", "utilities_fixed_per",
               "utilities_fixed_amount")


def upgrade() -> None:
    conn = op.get_bind()
    admins = conn.execute(sa.text("SELECT id FROM users WHERE role = 'admin'")).scalars().all()
    now = datetime.now().replace(microsecond=0)
    for uid in admins:
        conn.execute(sa.text("UPDATE users SET role = 'director', token_version = token_version + 1 WHERE id = :id"), {"id": uid})
        conn.execute(
            sa.text("INSERT INTO audit_log (ts, user_id, actor, entity, entity_id, action, before, after, reason) "
                    "VALUES (:ts, NULL, 'migration', 'user', :id, 'role_change', :b, :a, :r)"),
            {"ts": now, "id": uid, "b": '{"role": "admin"}', "a": '{"role": "director"}',
             "r": "Канон 0.3.0: роль адміністратора скасовано (D-4)"},
        )
    conn.execute(sa.text("DELETE FROM settings WHERE key IN :keys").bindparams(sa.bindparam("keys", expanding=True)),
                 {"keys": list(LEGACY_KEYS)})


def downgrade() -> None:
    pass
