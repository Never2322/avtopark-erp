"""haul_marks

Личный счётчик ходок водителя самосвала: одна строка на ходку (время нажатия, часовой пояс телефона, день по месту водителя).
Записи видит только сам водитель, в отчёты и журнал действий они не идут. Новое право hauls.use_own выдаётся водителям.

Revision ID: 0022
Revises: 0021
"""
import sqlalchemy as sa
from alembic import op

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('haul_marks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('marked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('tz_min', sa.Integer(), server_default='0', nullable=False),
    sa.Column('client_id', sa.String(length=64), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_haul_marks_employee_id_employees')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_haul_marks'))
    )
    op.create_index('ix_haul_marks_employee_day', 'haul_marks', ['employee_id', 'local_date', 'marked_at'], unique=False)
    op.create_index('uq_haul_marks_client', 'haul_marks', ['employee_id', 'client_id'], unique=True, postgresql_where=sa.text("client_id <> ''"))


def downgrade():
    op.drop_index('uq_haul_marks_client', table_name='haul_marks', postgresql_where=sa.text("client_id <> ''"))
    op.drop_index('ix_haul_marks_employee_day', table_name='haul_marks')
    op.drop_table('haul_marks')
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'hauls.use_own')")
    op.execute("DELETE FROM permissions WHERE code = 'hauls.use_own'")
