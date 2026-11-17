"""weigh_tickets

Тоннаж: талоны весов с полигона (одна запись на ходку: фото, заметка, тонны) и галочка «Вести тоннаж» на технике.
Мусоровозы и самосвалы, которые уже есть в базе, получают галочку сразу.

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from alembic import op

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('vehicles', sa.Column('tonnage', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.execute("UPDATE vehicles SET tonnage = true WHERE type_id IN (SELECT id FROM vehicle_types WHERE code IN ('truck_waste', 'truck_dump') "
               "OR lower(name) LIKE '%мусоровоз%' OR lower(name) LIKE '%самосвал%')")
    op.create_table('weigh_tickets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('driver_id', sa.Integer(), nullable=True),
    sa.Column('ticket_date', sa.Date(), nullable=False),
    sa.Column('tons', sa.Numeric(precision=8, scale=3), nullable=True),
    sa.Column('tons_source', sa.String(length=10), server_default='', nullable=False),
    sa.Column('tons_guess', sa.Numeric(precision=8, scale=3), nullable=True),
    sa.Column('doc_number', sa.String(length=60), server_default='', nullable=False),
    sa.Column('read_note', sa.String(length=300), server_default='', nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.String(length=500), server_default='', nullable=False),
    sa.Column('client_id', sa.String(length=64), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint('tons IS NULL OR (tons > 0 AND tons <= 200)', name=op.f('ck_weigh_tickets_tons')),
    sa.CheckConstraint("tons_source IN ('', 'read', 'manual')", name=op.f('ck_weigh_tickets_tons_source')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_weigh_tickets_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_weigh_tickets_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['driver_id'], ['employees.id'], name=op.f('fk_weigh_tickets_driver_id_employees')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_weigh_tickets_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_weigh_tickets_updated_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_weigh_tickets_vehicle_id_vehicles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_weigh_tickets'))
    )
    op.create_index(op.f('ix_weigh_tickets_legal_entity_id'), 'weigh_tickets', ['legal_entity_id'], unique=False)
    op.create_index(op.f('ix_weigh_tickets_ticket_date'), 'weigh_tickets', ['ticket_date'], unique=False)
    op.create_index('ix_weigh_tickets_vehicle_date', 'weigh_tickets', ['vehicle_id', 'ticket_date'], unique=False)
    op.create_index(op.f('ix_weigh_tickets_vehicle_id'), 'weigh_tickets', ['vehicle_id'], unique=False)
    op.create_index('uq_weigh_tickets_client', 'weigh_tickets', ['created_by_id', 'client_id'], unique=True, postgresql_where=sa.text("client_id <> ''"))

    # фото талона лежит в той же таблице, что накладные и чеки: у фото ровно один «хозяин» из трёх
    op.add_column('entry_photos', sa.Column('weigh_ticket_id', sa.Integer(), nullable=True))
    op.create_foreign_key(op.f('fk_entry_photos_weigh_ticket_id_weigh_tickets'), 'entry_photos', 'weigh_tickets', ['weigh_ticket_id'], ['id'])
    op.create_index(op.f('ix_entry_photos_weigh_ticket_id'), 'entry_photos', ['weigh_ticket_id'], unique=False)
    op.drop_constraint(op.f('ck_entry_photos_owner'), 'entry_photos', type_='check')
    op.create_check_constraint(op.f('ck_entry_photos_owner'), 'entry_photos',
                               '(trip_id IS NOT NULL)::int + (fuel_fill_id IS NOT NULL)::int + (weigh_ticket_id IS NOT NULL)::int = 1')


def downgrade():
    op.drop_constraint(op.f('ck_entry_photos_owner'), 'entry_photos', type_='check')
    op.execute('DELETE FROM entry_photos WHERE weigh_ticket_id IS NOT NULL')
    op.create_check_constraint(op.f('ck_entry_photos_owner'), 'entry_photos', '(trip_id IS NOT NULL) <> (fuel_fill_id IS NOT NULL)')
    op.drop_index(op.f('ix_entry_photos_weigh_ticket_id'), table_name='entry_photos')
    op.drop_constraint(op.f('fk_entry_photos_weigh_ticket_id_weigh_tickets'), 'entry_photos', type_='foreignkey')
    op.drop_column('entry_photos', 'weigh_ticket_id')
    op.drop_table('weigh_tickets')
    op.drop_column('vehicles', 'tonnage')
