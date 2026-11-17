"""hours_by_mechanic

Водитель больше не вносит свои часы: их вносят директор, диспетчер и механик.
1) У записей часов появляется источник «mechanic» (внёс механик).
2) Право «worklog.create_own» («Отмечать свои часы») снимается у всех ролей и удаляется. Новое право механика
   «worklog.enter» программа выдаст сама при запуске (по умолчанию его получает роль «Механик»).
Старые записи, внесённые водителями, остаются как есть.

Revision ID: 0021
Revises: 0020
"""
from alembic import op

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f('ck_work_logs_source'), 'work_logs', type_='check')
    op.create_check_constraint(op.f('ck_work_logs_source'), 'work_logs', "source IN ('driver', 'staff', 'mechanic')")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'worklog.create_own')")
    op.execute("DELETE FROM permissions WHERE code = 'worklog.create_own'")


def downgrade():
    op.execute("UPDATE work_logs SET source = 'staff' WHERE source = 'mechanic'")
    op.drop_constraint(op.f('ck_work_logs_source'), 'work_logs', type_='check')
    op.create_check_constraint(op.f('ck_work_logs_source'), 'work_logs', "source IN ('driver', 'staff')")
