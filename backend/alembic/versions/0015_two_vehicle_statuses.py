"""two_vehicle_statuses

Статусов техники теперь два: «На линии» (in_work) и «В ремонте» (in_repair).
Ожидает ремонта -> в ремонте; выходной, на стоянке, в аренде, не используется -> на линии;
списанные машины уходят в архив. Историю смен статусов (в ней запрещено менять записи) не трогаем:
у каждой переведённой машины добавляется запись «статусы упрощены».

Revision ID: 0015
Revises: 0014
"""
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

NEW_CHECK = "status IN ('in_work', 'in_repair')"
OLD_CHECK = "status IN ('in_work', 'in_repair', 'awaiting_repair', 'parked', 'day_off', 'rented', 'unused', 'written_off')"


def upgrade():
    op.drop_constraint(op.f('ck_vehicles_status'), 'vehicles', type_='check')
    # запись в историю, чтобы по ней было видно, что статус сменился не по воле человека
    op.execute("""
    INSERT INTO vehicle_status_history (vehicle_id, from_status, status, reason, changed_at)
    SELECT id, status,
           CASE WHEN status = 'awaiting_repair' THEN 'in_repair' ELSE 'in_work' END,
           'Статусы упрощены до двух: на линии и в ремонте', now()
    FROM vehicles WHERE status NOT IN ('in_work', 'in_repair')
    """)
    op.execute("UPDATE vehicles SET archived_at = COALESCE(archived_at, now()) WHERE status = 'written_off'")
    op.execute("UPDATE vehicles SET status = 'in_repair', status_changed_at = now() WHERE status = 'awaiting_repair'")
    op.execute("UPDATE vehicles SET status = 'in_work', status_changed_at = now() "
               "WHERE status IN ('parked', 'day_off', 'rented', 'unused', 'written_off')")
    op.create_check_constraint(op.f('ck_vehicles_status'), 'vehicles', NEW_CHECK)


def downgrade():
    op.drop_constraint(op.f('ck_vehicles_status'), 'vehicles', type_='check')
    op.create_check_constraint(op.f('ck_vehicles_status'), 'vehicles', OLD_CHECK)
