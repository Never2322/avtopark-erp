"""garbage_pricing

Мусоровозы: тариф «за тонну с порогом за день» (до порога одна ставка, от порога другая, на все тонны дня),
цена за тонну для заказчика у машины и в каждом талоне весов (стоимость рейса = тонны × цена).

Revision ID: 0025
Revises: 0024
"""
import sqlalchemy as sa
from alembic import op

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tariffs', sa.Column('rate_high', sa.Numeric(14, 2), nullable=True))
    op.add_column('tariffs', sa.Column('tier_tons', sa.Numeric(6, 2), nullable=True))
    op.drop_constraint(op.f('ck_tariffs_unit'), 'tariffs', type_='check')
    op.create_check_constraint(op.f('ck_tariffs_unit'), 'tariffs', "unit IN ('km', 'hour', 'ton', 'ton_tier', 'trip', 'day')")
    op.create_check_constraint(op.f('ck_tariffs_tier'), 'tariffs',
                               "unit <> 'ton_tier' OR (rate_high IS NOT NULL AND rate_high >= 0 AND tier_tons IS NOT NULL AND tier_tons > 0)")
    op.add_column('vehicles', sa.Column('ton_price', sa.Numeric(14, 2), nullable=True))
    op.create_check_constraint(op.f('ck_vehicles_ton_price'), 'vehicles', "ton_price IS NULL OR ton_price >= 0")
    op.add_column('weigh_tickets', sa.Column('price', sa.Numeric(14, 2), nullable=True))
    op.create_check_constraint(op.f('ck_weigh_tickets_price'), 'weigh_tickets', "price IS NULL OR price >= 0")


def downgrade():
    op.drop_constraint(op.f('ck_weigh_tickets_price'), 'weigh_tickets', type_='check')
    op.drop_column('weigh_tickets', 'price')
    op.drop_constraint(op.f('ck_vehicles_ton_price'), 'vehicles', type_='check')
    op.drop_column('vehicles', 'ton_price')
    op.drop_constraint(op.f('ck_tariffs_tier'), 'tariffs', type_='check')
    op.execute("UPDATE tariffs SET unit = 'ton' WHERE unit = 'ton_tier'")
    op.drop_constraint(op.f('ck_tariffs_unit'), 'tariffs', type_='check')
    op.create_check_constraint(op.f('ck_tariffs_unit'), 'tariffs', "unit IN ('km', 'hour', 'ton', 'trip', 'day')")
    op.drop_column('tariffs', 'tier_tons')
    op.drop_column('tariffs', 'rate_high')
