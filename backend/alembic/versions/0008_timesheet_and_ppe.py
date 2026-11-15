"""Табель и СИЗ

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ppe_norms',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('wear_months', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=300), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint('wear_months IS NULL OR (wear_months >= 1 AND wear_months <= 120)', name=op.f('ck_ppe_norms_wear_months')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_ppe_norms_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_ppe_norms_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_ppe_norms_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ppe_norms'))
    )
    op.create_index('uq_ppe_norms_name_active', 'ppe_norms', [sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_table('ppe_issues',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('ppe_id', sa.Integer(), nullable=False),
    sa.Column('issue_date', sa.Date(), nullable=False),
    sa.Column('qty', sa.Integer(), server_default='1', nullable=False),
    sa.Column('size', sa.String(length=20), server_default='', nullable=False),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('returned_date', sa.Date(), nullable=True),
    sa.Column('note', sa.String(length=300), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint('due_date IS NULL OR due_date >= issue_date', name=op.f('ck_ppe_issues_due_date')),
    sa.CheckConstraint('qty >= 1 AND qty <= 1000', name=op.f('ck_ppe_issues_qty')),
    sa.CheckConstraint('returned_date IS NULL OR returned_date >= issue_date', name=op.f('ck_ppe_issues_returned_date')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_ppe_issues_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_ppe_issues_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_ppe_issues_employee_id_employees')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_ppe_issues_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['ppe_id'], ['ppe_norms.id'], name=op.f('fk_ppe_issues_ppe_id_ppe_norms')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_ppe_issues_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ppe_issues'))
    )
    op.create_index(op.f('ix_ppe_issues_due_date'), 'ppe_issues', ['due_date'], unique=False)
    op.create_index(op.f('ix_ppe_issues_employee_id'), 'ppe_issues', ['employee_id'], unique=False)
    op.create_index(op.f('ix_ppe_issues_issue_date'), 'ppe_issues', ['issue_date'], unique=False)
    op.create_index(op.f('ix_ppe_issues_legal_entity_id'), 'ppe_issues', ['legal_entity_id'], unique=False)
    op.create_index(op.f('ix_ppe_issues_ppe_id'), 'ppe_issues', ['ppe_id'], unique=False)
    op.create_table('timesheet_marks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('work_date', sa.Date(), nullable=False),
    sa.Column('code', sa.String(length=3), nullable=False),
    sa.Column('hours', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("code IN ('Я', 'Н', 'РВ', 'С', 'К', 'ОТ', 'ОД', 'ДО', 'Р', 'ОЖ', 'Б', 'Т', 'ПР', 'НН', 'РП', 'В')", name=op.f('ck_timesheet_marks_code')),
    sa.CheckConstraint('hours IS NULL OR (hours >= 0 AND hours <= 24)', name=op.f('ck_timesheet_marks_hours')),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_timesheet_marks_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_timesheet_marks_employee_id_employees')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_timesheet_marks_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_timesheet_marks_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_timesheet_marks')),
    sa.UniqueConstraint('legal_entity_id', 'employee_id', 'work_date', name=op.f('uq_timesheet_marks_legal_entity_id'))
    )
    op.create_index(op.f('ix_timesheet_marks_employee_id'), 'timesheet_marks', ['employee_id'], unique=False)
    op.create_index(op.f('ix_timesheet_marks_legal_entity_id'), 'timesheet_marks', ['legal_entity_id'], unique=False)
    op.create_index('ix_timesheet_marks_period', 'timesheet_marks', ['legal_entity_id', 'work_date'], unique=False)


def downgrade():
    op.drop_index('ix_timesheet_marks_period', table_name='timesheet_marks')
    op.drop_index(op.f('ix_timesheet_marks_legal_entity_id'), table_name='timesheet_marks')
    op.drop_index(op.f('ix_timesheet_marks_employee_id'), table_name='timesheet_marks')
    op.drop_table('timesheet_marks')
    op.drop_index(op.f('ix_ppe_issues_ppe_id'), table_name='ppe_issues')
    op.drop_index(op.f('ix_ppe_issues_legal_entity_id'), table_name='ppe_issues')
    op.drop_index(op.f('ix_ppe_issues_issue_date'), table_name='ppe_issues')
    op.drop_index(op.f('ix_ppe_issues_employee_id'), table_name='ppe_issues')
    op.drop_index(op.f('ix_ppe_issues_due_date'), table_name='ppe_issues')
    op.drop_table('ppe_issues')
    op.drop_index('uq_ppe_norms_name_active', table_name='ppe_norms', postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_table('ppe_norms')
