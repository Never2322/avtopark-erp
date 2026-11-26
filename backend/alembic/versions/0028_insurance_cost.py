"""insurance cost

Стоимость полиса ОСАГО или КАСКО в личном деле техники: попадает в затраты по машине по дате выдачи полиса.

Revision ID: 0028
Revises: 0027
"""
import sqlalchemy as sa
from alembic import op

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('dossier_docs', sa.Column('cost', sa.Numeric(14, 2), nullable=True))
    op.create_check_constraint('ck_dossier_docs_cost', 'dossier_docs', 'cost IS NULL OR cost >= 0')


def downgrade():
    op.drop_constraint('ck_dossier_docs_cost', 'dossier_docs', type_='check')
    op.drop_column('dossier_docs', 'cost')
