"""engine hours, bill categories and budgets, garbage day rates

- заправка: моточасы вместо пробега для спецтехники;
- счёт на ремонт: категория (запчасти, жидкости, расходники) и бюджет на месяц по каждой категории;
- зарплата мусоровозов: ставка за тонну, проставленная на день водителю или грузчику.

Revision ID: 0030
Revises: 0029
"""
import sqlalchemy as sa
from alembic import op

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None

CATS = "('parts', 'fluids', 'consumables')"


def upgrade():
    op.add_column('fuel_fills', sa.Column('engine_hours', sa.Numeric(9, 1), nullable=True))
    op.create_check_constraint('ck_fuel_fills_engine_hours', 'fuel_fills', 'engine_hours IS NULL OR engine_hours >= 0')
    op.add_column('repair_bills', sa.Column('category', sa.String(12), nullable=True))
    op.create_check_constraint('ck_repair_bills_category', 'repair_bills', f'category IS NULL OR category IN {CATS}')
    op.create_table(
        'bill_budgets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('month', sa.Date(), nullable=False),
        sa.Column('category', sa.String(12), nullable=False),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.UniqueConstraint('month', 'category', name='uq_bill_budgets'),
        sa.CheckConstraint('amount >= 0', name='ck_bill_budgets_amount'),
        sa.CheckConstraint(f'category IN {CATS}', name='ck_bill_budgets_category'),
    )
    op.create_table(
        'garbage_day_rates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('legal_entity_id', sa.Integer(), sa.ForeignKey('legal_entities.id'), nullable=False, index=True),
        sa.Column('work_date', sa.Date(), nullable=False, index=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
        sa.Column('rate', sa.Numeric(14, 2), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.UniqueConstraint('work_date', 'vehicle_id', 'employee_id', name='uq_garbage_day_rates'),
        sa.CheckConstraint('rate >= 0', name='ck_garbage_day_rates_rate'),
    )


def downgrade():
    op.drop_table('garbage_day_rates')
    op.drop_table('bill_budgets')
    op.drop_constraint('ck_repair_bills_category', 'repair_bills', type_='check')
    op.drop_column('repair_bills', 'category')
    op.drop_constraint('ck_fuel_fills_engine_hours', 'fuel_fills', type_='check')
    op.drop_column('fuel_fills', 'engine_hours')
