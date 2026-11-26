"""crew vehicles

Какие машины показываются в «Бригадах мусоровозов». Пусто — как раньше, само (мусоровозы и машины с тоннажем);
«да» — добавлена вручную, «нет» — убрана из бригад.

Revision ID: 0027
Revises: 0026
"""
import sqlalchemy as sa
from alembic import op

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('vehicles', sa.Column('in_crews', sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column('vehicles', 'in_crews')
