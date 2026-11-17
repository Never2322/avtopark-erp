"""drop_timesheet_edit

Табель теперь показывает деньги за часы работы и ремонта, а не отметки: право «ставить отметки в табеле» больше не нужно.
Строка права и её выдачи ролям удаляются, чтобы не висеть в списке прав.

Revision ID: 0019
Revises: 0018
"""
from alembic import op

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'timesheet.edit')")
    op.execute("DELETE FROM permissions WHERE code = 'timesheet.edit'")


def downgrade():
    # право возвращать незачем: без отметок оно ничего не делает
    pass
