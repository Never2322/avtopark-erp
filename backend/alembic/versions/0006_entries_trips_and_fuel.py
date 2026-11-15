"""Записи водителей: рейсы, заправки, фото к ним; объём бака и норма расхода; источник показаний «Заправка»

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f('ck_vehicle_readings_source'), 'vehicle_readings', type_='check')
    op.create_check_constraint(op.f('ck_vehicle_readings_source'), 'vehicle_readings',
                               "source IN ('manual', 'waybill', 'gps', 'import', 'fuel')")
    op.create_table('fuel_fills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('driver_id', sa.Integer(), nullable=True),
    sa.Column('fill_date', sa.Date(), nullable=False),
    sa.Column('liters', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('odometer', sa.Integer(), nullable=True),
    sa.Column('full_tank', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('station', sa.String(length=80), server_default='', nullable=False),
    sa.Column('note', sa.String(length=300), server_default='', nullable=False),
    sa.Column('client_id', sa.String(length=64), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint('amount >= 0', name=op.f('ck_fuel_fills_amount')),
    sa.CheckConstraint('liters > 0', name=op.f('ck_fuel_fills_liters')),
    sa.CheckConstraint('odometer IS NULL OR odometer >= 0', name=op.f('ck_fuel_fills_odometer')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_fuel_fills_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_fuel_fills_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['driver_id'], ['employees.id'], name=op.f('fk_fuel_fills_driver_id_employees')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_fuel_fills_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_fuel_fills_updated_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_fuel_fills_vehicle_id_vehicles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fuel_fills'))
    )
    op.create_index(op.f('ix_fuel_fills_fill_date'), 'fuel_fills', ['fill_date'], unique=False)
    op.create_index(op.f('ix_fuel_fills_legal_entity_id'), 'fuel_fills', ['legal_entity_id'], unique=False)
    op.create_index('ix_fuel_fills_vehicle_date', 'fuel_fills', ['vehicle_id', 'fill_date'], unique=False)
    op.create_index(op.f('ix_fuel_fills_vehicle_id'), 'fuel_fills', ['vehicle_id'], unique=False)
    op.create_index('uq_fuel_fills_client', 'fuel_fills', ['created_by_id', 'client_id'], unique=True, postgresql_where=sa.text("client_id <> ''"))
    op.create_table('trips',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('driver_id', sa.Integer(), nullable=True),
    sa.Column('trip_date', sa.Date(), nullable=False),
    sa.Column('route', sa.String(length=200), nullable=False),
    sa.Column('customer', sa.String(length=120), server_default='', nullable=False),
    sa.Column('cargo', sa.String(length=200), server_default='', nullable=False),
    sa.Column('cargo_kg', sa.Numeric(precision=12, scale=1), nullable=True),
    sa.Column('doc_number', sa.String(length=60), server_default='', nullable=False),
    sa.Column('km', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=500), server_default='', nullable=False),
    sa.Column('client_id', sa.String(length=64), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint('cargo_kg IS NULL OR cargo_kg >= 0', name=op.f('ck_trips_cargo')),
    sa.CheckConstraint('km IS NULL OR km >= 0', name=op.f('ck_trips_km')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_trips_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_trips_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['driver_id'], ['employees.id'], name=op.f('fk_trips_driver_id_employees')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_trips_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_trips_updated_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_trips_vehicle_id_vehicles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trips'))
    )
    op.create_index(op.f('ix_trips_legal_entity_id'), 'trips', ['legal_entity_id'], unique=False)
    op.create_index(op.f('ix_trips_trip_date'), 'trips', ['trip_date'], unique=False)
    op.create_index('ix_trips_vehicle_date', 'trips', ['vehicle_id', 'trip_date'], unique=False)
    op.create_index(op.f('ix_trips_vehicle_id'), 'trips', ['vehicle_id'], unique=False)
    op.create_index('uq_trips_client', 'trips', ['created_by_id', 'client_id'], unique=True, postgresql_where=sa.text("client_id <> ''"))
    op.create_table('entry_photos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trip_id', sa.Integer(), nullable=True),
    sa.Column('fuel_fill_id', sa.Integer(), nullable=True),
    sa.Column('file_id', sa.BigInteger(), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('added_by_id', sa.Integer(), nullable=True),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('removed_by_id', sa.Integer(), nullable=True),
    sa.Column('remove_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint('(trip_id IS NOT NULL) <> (fuel_fill_id IS NOT NULL)', name=op.f('ck_entry_photos_owner')),
    sa.ForeignKeyConstraint(['added_by_id'], ['users.id'], name=op.f('fk_entry_photos_added_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['file_id'], ['stored_files.id'], name=op.f('fk_entry_photos_file_id_stored_files')),
    sa.ForeignKeyConstraint(['fuel_fill_id'], ['fuel_fills.id'], name=op.f('fk_entry_photos_fuel_fill_id_fuel_fills')),
    sa.ForeignKeyConstraint(['removed_by_id'], ['users.id'], name=op.f('fk_entry_photos_removed_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['trip_id'], ['trips.id'], name=op.f('fk_entry_photos_trip_id_trips')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_entry_photos'))
    )
    op.create_index(op.f('ix_entry_photos_file_id'), 'entry_photos', ['file_id'], unique=False)
    op.create_index(op.f('ix_entry_photos_fuel_fill_id'), 'entry_photos', ['fuel_fill_id'], unique=False)
    op.create_index(op.f('ix_entry_photos_trip_id'), 'entry_photos', ['trip_id'], unique=False)
    op.add_column('vehicle_specs', sa.Column('tank_liters', sa.Numeric(precision=6, scale=1), nullable=True))
    op.add_column('vehicle_specs', sa.Column('fuel_norm', sa.Numeric(precision=5, scale=1), nullable=True))


def downgrade():
    op.drop_column('vehicle_specs', 'fuel_norm')
    op.drop_column('vehicle_specs', 'tank_liters')
    op.drop_index(op.f('ix_entry_photos_trip_id'), table_name='entry_photos')
    op.drop_index(op.f('ix_entry_photos_fuel_fill_id'), table_name='entry_photos')
    op.drop_index(op.f('ix_entry_photos_file_id'), table_name='entry_photos')
    op.drop_table('entry_photos')
    op.drop_index('uq_trips_client', table_name='trips', postgresql_where=sa.text("client_id <> ''"))
    op.drop_index(op.f('ix_trips_vehicle_id'), table_name='trips')
    op.drop_index('ix_trips_vehicle_date', table_name='trips')
    op.drop_index(op.f('ix_trips_trip_date'), table_name='trips')
    op.drop_index(op.f('ix_trips_legal_entity_id'), table_name='trips')
    op.drop_table('trips')
    op.drop_index('uq_fuel_fills_client', table_name='fuel_fills', postgresql_where=sa.text("client_id <> ''"))
    op.drop_index(op.f('ix_fuel_fills_vehicle_id'), table_name='fuel_fills')
    op.drop_index('ix_fuel_fills_vehicle_date', table_name='fuel_fills')
    op.drop_index(op.f('ix_fuel_fills_legal_entity_id'), table_name='fuel_fills')
    op.drop_index(op.f('ix_fuel_fills_fill_date'), table_name='fuel_fills')
    op.drop_table('fuel_fills')
    op.drop_constraint(op.f('ck_vehicle_readings_source'), 'vehicle_readings', type_='check')
    # показания неизменяемы (их нельзя ни стереть, ни переписать), поэтому уже сохранённые «из заправок» остаются; новые такого вида не принимаются
    op.execute("ALTER TABLE vehicle_readings ADD CONSTRAINT ck_vehicle_readings_source "
               "CHECK (source IN ('manual', 'waybill', 'gps', 'import')) NOT VALID")
