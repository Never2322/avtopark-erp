"""garbage_routes

Маршруты мусоровозов (название и перечень улиц) и расписание: какой мусоровоз на каком маршруте
по дням недели или на одну дату. Права garbage_routes.*, route_plan.* и route_plan.view_own.

Revision ID: 0023
Revises: 0022
"""
import sqlalchemy as sa
from alembic import op

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None

_CODES = ("garbage_routes.view", "garbage_routes.create", "garbage_routes.edit", "garbage_routes.void",
          "route_plan.view", "route_plan.create", "route_plan.edit", "route_plan.void", "route_plan.view_own")


def _audit_columns():
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('archived_by_id', sa.Integer(), nullable=True),
        sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    ]


def _audit_keys(table):
    return [
        sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f(f'fk_{table}_archived_by_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f(f'fk_{table}_created_by_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f(f'fk_{table}_updated_by_id_users'), ondelete='SET NULL'),
    ]


def upgrade():
    op.create_table('garbage_routes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('streets', sa.Text(), server_default='', nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    *_audit_columns(),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_garbage_routes_legal_entity_id_legal_entities')),
    *_audit_keys('garbage_routes'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_garbage_routes'))
    )
    op.create_index(op.f('ix_garbage_routes_legal_entity_id'), 'garbage_routes', ['legal_entity_id'], unique=False)
    op.create_index('uq_garbage_routes_name_active', 'garbage_routes', ['legal_entity_id', sa.text('lower(name)')], unique=True,
                    postgresql_where=sa.text('archived_at IS NULL'))

    op.create_table('route_plans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('route_id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=6), server_default='weekly', nullable=False),
    sa.Column('mon', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('tue', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('wed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('thu', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('fri', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('sat', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('sun', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('on_date', sa.Date(), nullable=True),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    *_audit_columns(),
    sa.CheckConstraint("kind IN ('weekly', 'date')", name=op.f('ck_route_plans_kind')),
    sa.CheckConstraint("(kind = 'weekly' AND on_date IS NULL AND (mon OR tue OR wed OR thu OR fri OR sat OR sun)) "
                       "OR (kind = 'date' AND on_date IS NOT NULL AND NOT (mon OR tue OR wed OR thu OR fri OR sat OR sun))",
                       name=op.f('ck_route_plans_shape')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_route_plans_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['route_id'], ['garbage_routes.id'], name=op.f('fk_route_plans_route_id_garbage_routes')),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_route_plans_vehicle_id_vehicles')),
    *_audit_keys('route_plans'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_route_plans'))
    )
    op.create_index(op.f('ix_route_plans_legal_entity_id'), 'route_plans', ['legal_entity_id'], unique=False)
    op.create_index(op.f('ix_route_plans_route_id'), 'route_plans', ['route_id'], unique=False)
    op.create_index(op.f('ix_route_plans_vehicle_id'), 'route_plans', ['vehicle_id'], unique=False)
    op.create_index('ix_route_plans_date', 'route_plans', ['on_date'], unique=False)


def downgrade():
    op.drop_index('ix_route_plans_date', table_name='route_plans')
    op.drop_index(op.f('ix_route_plans_vehicle_id'), table_name='route_plans')
    op.drop_index(op.f('ix_route_plans_route_id'), table_name='route_plans')
    op.drop_index(op.f('ix_route_plans_legal_entity_id'), table_name='route_plans')
    op.drop_table('route_plans')
    op.drop_index('uq_garbage_routes_name_active', table_name='garbage_routes', postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_index(op.f('ix_garbage_routes_legal_entity_id'), table_name='garbage_routes')
    op.drop_table('garbage_routes')
    codes = ", ".join("'%s'" % c for c in _CODES)
    op.execute(f"DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({codes}))")
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
