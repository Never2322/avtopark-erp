"""worklog_tariff_manual

Часы работы и ремонта: тариф можно выбрать вручную при внесении. Отметка «выбран вручную» нужна, чтобы смена вида
(работа/ремонт) не подменяла выбранный человеком тариф тарифом из карточки сотрудника.

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from alembic import op

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('work_logs', sa.Column('tariff_manual', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade():
    op.drop_column('work_logs', 'tariff_manual')
