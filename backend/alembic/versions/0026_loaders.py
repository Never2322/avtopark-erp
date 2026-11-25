"""loaders

Грузчики мусоровозов: закрепление грузчика за машиной на период (отдельно от водителей, чтобы грузчик не стал «водителем»
машины в талонах, зарплате и маршрутах) и замечания по маршруту, которые грузчик отправляет с телефона.

Revision ID: 0026
Revises: 0025
"""
import sqlalchemy as sa
from alembic import op

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'loader_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name=op.f('fk_loader_assignments_vehicle_id_vehicles')), nullable=False),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id', name=op.f('fk_loader_assignments_employee_id_employees')), nullable=False),
        sa.Column('started_on', sa.Date(), nullable=False),
        sa.Column('ended_on', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL', name=op.f('fk_loader_assignments_created_by_id_users')), nullable=True),
        sa.CheckConstraint('ended_on IS NULL OR ended_on >= started_on', name=op.f('ck_loader_assignments_dates')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_loader_assignments')),
    )
    op.create_index(op.f('ix_loader_assignments_vehicle_id'), 'loader_assignments', ['vehicle_id'])
    op.create_index(op.f('ix_loader_assignments_employee_id'), 'loader_assignments', ['employee_id'])

    op.create_table(
        'route_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('legal_entity_id', sa.Integer(), sa.ForeignKey('legal_entities.id', name=op.f('fk_route_notes_legal_entity_id_legal_entities')), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name=op.f('fk_route_notes_vehicle_id_vehicles')), nullable=False),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id', name=op.f('fk_route_notes_employee_id_employees')), nullable=False),
        sa.Column('note_date', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('street', sa.String(200), server_default='', nullable=False),
        sa.Column('note', sa.String(500), server_default='', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL', name=op.f('fk_route_notes_resolved_by_id_users')), nullable=True),
        sa.CheckConstraint("kind IN ('not_taken', 'overflow', 'broken', 'blocked', 'other')", name=op.f('ck_route_notes_kind')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_route_notes')),
    )
    op.create_index(op.f('ix_route_notes_legal_entity_id'), 'route_notes', ['legal_entity_id'])
    op.create_index('ix_route_notes_date', 'route_notes', ['note_date'])


def downgrade():
    op.execute("DELETE FROM user_roles WHERE role_id IN (SELECT id FROM roles WHERE code = 'loader')")
    op.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code = 'loader')")
    op.execute("DELETE FROM roles WHERE code = 'loader'")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('loaders.view_own', 'route_notes.view', 'route_notes.resolve'))")
    op.execute("DELETE FROM permissions WHERE code IN ('loaders.view_own', 'route_notes.view', 'route_notes.resolve')")
    op.drop_index('ix_route_notes_date', table_name='route_notes')
    op.drop_index(op.f('ix_route_notes_legal_entity_id'), table_name='route_notes')
    op.drop_table('route_notes')
    op.drop_index(op.f('ix_loader_assignments_employee_id'), table_name='loader_assignments')
    op.drop_index(op.f('ix_loader_assignments_vehicle_id'), table_name='loader_assignments')
    op.drop_table('loader_assignments')
