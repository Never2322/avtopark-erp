"""События сотрудников и календарь

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('calendar_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('all_day', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('color', sa.String(length=8), server_default='teal', nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=True),
    sa.Column('employee_id', sa.Integer(), nullable=True),
    sa.Column('remind_min', sa.Integer(), nullable=True),
    sa.Column('done', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('note', sa.String(length=500), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint("color IN ('teal', 'blue', 'amber', 'red', 'green', 'violet')", name=op.f('ck_calendar_events_color')),
    sa.CheckConstraint('end_at IS NULL OR end_at >= start_at', name=op.f('ck_calendar_events_dates')),
    sa.CheckConstraint('remind_min IS NULL OR (remind_min >= 0 AND remind_min <= 43200)', name=op.f('ck_calendar_events_remind_min')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_calendar_events_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_calendar_events_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_calendar_events_employee_id_employees')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_calendar_events_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_calendar_events_updated_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_calendar_events_vehicle_id_vehicles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calendar_events'))
    )
    op.create_index(op.f('ix_calendar_events_legal_entity_id'), 'calendar_events', ['legal_entity_id'], unique=False)
    op.create_index(op.f('ix_calendar_events_start_at'), 'calendar_events', ['start_at'], unique=False)
    op.create_table('employee_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=12), server_default='other', nullable=False),
    sa.Column('event_date', sa.Date(), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('description', sa.String(length=300), server_default='', nullable=False),
    sa.Column('controlled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('note', sa.String(length=500), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint("kind IN ('medical', 'license', 'briefing', 'violation', 'accident', 'bonus', 'fine', 'other')", name=op.f('ck_employee_events_kind')),
    sa.CheckConstraint('amount >= 0', name=op.f('ck_employee_events_amount')),
    sa.CheckConstraint('event_date IS NULL OR due_date IS NULL OR due_date >= event_date', name=op.f('ck_employee_events_dates')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_employee_events_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_employee_events_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_employee_events_employee_id_employees')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_employee_events_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_employee_events_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employee_events'))
    )
    op.create_index(op.f('ix_employee_events_due_date'), 'employee_events', ['due_date'], unique=False)
    op.create_index(op.f('ix_employee_events_employee_id'), 'employee_events', ['employee_id'], unique=False)
    op.create_index(op.f('ix_employee_events_event_date'), 'employee_events', ['event_date'], unique=False)
    op.create_index(op.f('ix_employee_events_legal_entity_id'), 'employee_events', ['legal_entity_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_employee_events_legal_entity_id'), table_name='employee_events')
    op.drop_index(op.f('ix_employee_events_event_date'), table_name='employee_events')
    op.drop_index(op.f('ix_employee_events_employee_id'), table_name='employee_events')
    op.drop_index(op.f('ix_employee_events_due_date'), table_name='employee_events')
    op.drop_table('employee_events')
    op.drop_index(op.f('ix_calendar_events_start_at'), table_name='calendar_events')
    op.drop_index(op.f('ix_calendar_events_legal_entity_id'), table_name='calendar_events')
    op.drop_table('calendar_events')
