"""worklog_and_pay_tariffs

Зарплатный табель: у тарифа появился вид (для заказчика, оплата работы, оплата ремонта), у сотрудника два тарифа
(за работу и за ремонт), новая таблица work_logs: сколько часов человек работал или ремонтировал в день.

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tariffs', sa.Column('kind', sa.String(10), server_default='customer', nullable=False))
    op.create_check_constraint(op.f('ck_tariffs_kind'), 'tariffs', "kind IN ('customer', 'work', 'repair')")
    op.add_column('employees', sa.Column('work_tariff_id', sa.Integer(), sa.ForeignKey('tariffs.id', ondelete='SET NULL', name=op.f('fk_employees_work_tariff_id_tariffs')), nullable=True))
    op.add_column('employees', sa.Column('repair_tariff_id', sa.Integer(), sa.ForeignKey('tariffs.id', ondelete='SET NULL', name=op.f('fk_employees_repair_tariff_id_tariffs')), nullable=True))
    op.create_table(
        'work_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL', name=op.f('fk_work_logs_created_by_id_users'))),
        sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL', name=op.f('fk_work_logs_updated_by_id_users'))),
        sa.Column('archived_at', sa.DateTime(timezone=True)),
        sa.Column('archived_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL', name=op.f('fk_work_logs_archived_by_id_users'))),
        sa.Column('archive_reason', sa.String(300), server_default='', nullable=False),
        sa.Column('legal_entity_id', sa.Integer(), sa.ForeignKey('legal_entities.id', name=op.f('fk_work_logs_legal_entity_id_legal_entities')), nullable=False),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id', name=op.f('fk_work_logs_employee_id_employees')), nullable=False),
        sa.Column('work_date', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(8), nullable=False),
        sa.Column('hours', sa.Numeric(4, 1), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name=op.f('fk_work_logs_vehicle_id_vehicles'))),
        sa.Column('description', sa.String(500), server_default='', nullable=False),
        sa.Column('tariff_id', sa.Integer(), sa.ForeignKey('tariffs.id', ondelete='SET NULL', name=op.f('fk_work_logs_tariff_id_tariffs'))),
        sa.Column('tariff_name', sa.String(160), server_default='', nullable=False),
        sa.Column('unit', sa.String(8), server_default='hour', nullable=False),
        sa.Column('rate', sa.Numeric(14, 2), server_default='0', nullable=False),
        sa.Column('source', sa.String(8), server_default='staff', nullable=False),
        sa.Column('client_id', sa.String(64), server_default='', nullable=False),
        sa.CheckConstraint("kind IN ('work', 'repair')", name=op.f('ck_work_logs_kind')),
        sa.CheckConstraint("source IN ('driver', 'staff')", name=op.f('ck_work_logs_source')),
        sa.CheckConstraint("unit IN ('hour', 'day')", name=op.f('ck_work_logs_unit')),
        sa.CheckConstraint("hours > 0 AND hours <= 24", name=op.f('ck_work_logs_hours')),
        sa.CheckConstraint("rate >= 0", name=op.f('ck_work_logs_rate')),
    )
    op.create_index('ix_work_logs_legal_entity_id', 'work_logs', ['legal_entity_id'])
    op.create_index('ix_work_logs_employee_id', 'work_logs', ['employee_id'])
    op.create_index('ix_work_logs_period', 'work_logs', ['legal_entity_id', 'work_date'])
    op.create_index('ix_work_logs_employee_date', 'work_logs', ['employee_id', 'work_date'])
    op.create_index('uq_work_logs_client', 'work_logs', ['created_by_id', 'client_id'], unique=True, postgresql_where=sa.text("client_id <> ''"))


def downgrade():
    op.drop_table('work_logs')
    op.drop_column('employees', 'repair_tariff_id')
    op.drop_column('employees', 'work_tariff_id')
    op.drop_constraint(op.f('ck_tariffs_kind'), 'tariffs', type_='check')
    op.drop_column('tariffs', 'kind')
