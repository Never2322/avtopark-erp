"""shift_plans

Вкладка «Выход на работу»: сотрудник отвечает, выйдет ли он на работу в конкретный день.

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'shift_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL', name=op.f('fk_shift_plans_created_by_id_users'))),
        sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL', name=op.f('fk_shift_plans_updated_by_id_users'))),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id', name=op.f('fk_shift_plans_employee_id_employees')), nullable=False),
        sa.Column('plan_date', sa.Date(), nullable=False),
        sa.Column('will_come', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(300), server_default='', nullable=False),
        sa.Column('source', sa.String(8), server_default='driver', nullable=False),
        sa.CheckConstraint("source IN ('driver', 'staff')", name=op.f('ck_shift_plans_source')),
    )
    op.create_index('ix_shift_plans_employee_id', 'shift_plans', ['employee_id'])
    op.create_index('uq_shift_plans_employee_date', 'shift_plans', ['employee_id', 'plan_date'], unique=True)
    op.create_index('ix_shift_plans_date', 'shift_plans', ['plan_date'])


def downgrade():
    op.drop_table('shift_plans')
