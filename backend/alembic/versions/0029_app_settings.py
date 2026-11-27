"""app settings

Общие настройки программы (ключ — значение в JSON). Первая: порядок и названия пунктов меню, которые задают директор и администратор.

Revision ID: 0029
Revises: 0028
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('value', JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )


def downgrade():
    op.drop_table('app_settings')
