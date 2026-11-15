"""Детали (каталог и детали обращений), список «что купить», счета на ремонт

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('parts_catalog',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('name_key', sa.String(length=200), nullable=False),
    sa.Column('article', sa.String(length=60), server_default='', nullable=False),
    sa.Column('unit', sa.String(length=12), server_default='шт', nullable=False),
    sa.Column('category', sa.String(length=60), server_default='', nullable=False),
    sa.Column('uses', sa.Integer(), server_default='0', nullable=False),
    sa.Column('note', sa.String(length=300), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_parts_catalog_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_parts_catalog_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_parts_catalog_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_parts_catalog'))
    )
    op.create_index('uq_parts_catalog_key_active', 'parts_catalog', ['name_key'], unique=True, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_table('repair_bills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), server_default='uploaded', nullable=False),
    sa.Column('number', sa.String(length=40), nullable=False),
    sa.Column('bill_date', sa.Date(), nullable=False),
    sa.Column('supplier_id', sa.Integer(), nullable=True),
    sa.Column('supplier_name', sa.String(length=160), server_default='', nullable=False),
    sa.Column('supplier_inn', sa.String(length=12), server_default='', nullable=False),
    sa.Column('supplier_kpp', sa.String(length=9), server_default='', nullable=False),
    sa.Column('supplier_addr', sa.String(length=250), server_default='', nullable=False),
    sa.Column('supplier_phone', sa.String(length=40), server_default='', nullable=False),
    sa.Column('bank_name', sa.String(length=160), server_default='', nullable=False),
    sa.Column('bik', sa.String(length=9), server_default='', nullable=False),
    sa.Column('corr_account', sa.String(length=20), server_default='', nullable=False),
    sa.Column('account', sa.String(length=20), server_default='', nullable=False),
    sa.Column('basis', sa.String(length=120), server_default='', nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=True),
    sa.Column('request_id', sa.Integer(), nullable=True),
    sa.Column('vat_pct', sa.Integer(), server_default='22', nullable=False),
    sa.Column('total', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('vat', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=10), server_default='unpaid', nullable=False),
    sa.Column('paid_date', sa.Date(), nullable=True),
    sa.Column('note', sa.String(length=500), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint("(status = 'paid') = (paid_date IS NOT NULL)", name=op.f('ck_repair_bills_paid_date')),
    sa.CheckConstraint("kind IN ('issued', 'uploaded')", name=op.f('ck_repair_bills_kind')),
    sa.CheckConstraint("status IN ('unpaid', 'paid', 'cancelled')", name=op.f('ck_repair_bills_status')),
    sa.CheckConstraint('total >= 0 AND vat >= 0 AND vat <= total', name=op.f('ck_repair_bills_amounts')),
    sa.CheckConstraint('vat_pct IN (0, 5, 7, 10, 20, 22)', name=op.f('ck_repair_bills_vat_pct')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_repair_bills_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_repair_bills_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_repair_bills_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['request_id'], ['service_requests.id'], name=op.f('fk_repair_bills_request_id_service_requests')),
    sa.ForeignKeyConstraint(['supplier_id'], ['counterparties.id'], name=op.f('fk_repair_bills_supplier_id_counterparties')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_repair_bills_updated_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_repair_bills_vehicle_id_vehicles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_repair_bills'))
    )
    op.create_index(op.f('ix_repair_bills_bill_date'), 'repair_bills', ['bill_date'], unique=False)
    op.create_index(op.f('ix_repair_bills_legal_entity_id'), 'repair_bills', ['legal_entity_id'], unique=False)
    op.create_index('ix_repair_bills_number', 'repair_bills', ['legal_entity_id', 'bill_date'], unique=False)
    op.create_index(op.f('ix_repair_bills_request_id'), 'repair_bills', ['request_id'], unique=False)
    op.create_index(op.f('ix_repair_bills_vehicle_id'), 'repair_bills', ['vehicle_id'], unique=False)
    op.create_table('request_parts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('request_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('article', sa.String(length=60), server_default='', nullable=False),
    sa.Column('qty', sa.Numeric(precision=12, scale=3), server_default='1', nullable=False),
    sa.Column('unit', sa.String(length=12), server_default='шт', nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('added_by_id', sa.Integer(), nullable=True),
    sa.Column('bought_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('bought_by_id', sa.Integer(), nullable=True),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('removed_by_id', sa.Integer(), nullable=True),
    sa.CheckConstraint('qty > 0', name=op.f('ck_request_parts_qty')),
    sa.ForeignKeyConstraint(['added_by_id'], ['users.id'], name=op.f('fk_request_parts_added_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['bought_by_id'], ['users.id'], name=op.f('fk_request_parts_bought_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['removed_by_id'], ['users.id'], name=op.f('fk_request_parts_removed_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['request_id'], ['service_requests.id'], name=op.f('fk_request_parts_request_id_service_requests')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_request_parts'))
    )
    op.create_index(op.f('ix_request_parts_request_id'), 'request_parts', ['request_id'], unique=False)
    op.create_table('repair_bill_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('bill_id', sa.Integer(), nullable=False),
    sa.Column('file_id', sa.BigInteger(), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('added_by_id', sa.Integer(), nullable=True),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('removed_by_id', sa.Integer(), nullable=True),
    sa.Column('remove_reason', sa.String(length=300), server_default='', nullable=False),
    sa.ForeignKeyConstraint(['added_by_id'], ['users.id'], name=op.f('fk_repair_bill_files_added_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['bill_id'], ['repair_bills.id'], name=op.f('fk_repair_bill_files_bill_id_repair_bills')),
    sa.ForeignKeyConstraint(['file_id'], ['stored_files.id'], name=op.f('fk_repair_bill_files_file_id_stored_files')),
    sa.ForeignKeyConstraint(['removed_by_id'], ['users.id'], name=op.f('fk_repair_bill_files_removed_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_repair_bill_files'))
    )
    op.create_index(op.f('ix_repair_bill_files_bill_id'), 'repair_bill_files', ['bill_id'], unique=False)
    op.create_index(op.f('ix_repair_bill_files_file_id'), 'repair_bill_files', ['file_id'], unique=False)
    op.create_table('repair_bill_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('bill_id', sa.Integer(), nullable=False),
    sa.Column('pos', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('article', sa.String(length=60), server_default='', nullable=False),
    sa.Column('qty', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('unit', sa.String(length=12), server_default='шт', nullable=False),
    sa.Column('price', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('vat', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.CheckConstraint('qty > 0 AND price >= 0 AND amount >= 0 AND vat >= 0', name=op.f('ck_repair_bill_items_values')),
    sa.ForeignKeyConstraint(['bill_id'], ['repair_bills.id'], name=op.f('fk_repair_bill_items_bill_id_repair_bills'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_repair_bill_items'))
    )
    op.create_index(op.f('ix_repair_bill_items_bill_id'), 'repair_bill_items', ['bill_id'], unique=False)

    # Типовые детали для подсказок кладутся один раз, при первом создании таблицы.
    from app.parts_seed import SEED, norm
    catalog = sa.table("parts_catalog", sa.column("name"), sa.column("name_key"), sa.column("unit"), sa.column("category"))
    op.bulk_insert(catalog, [{"name": n, "name_key": norm(n), "unit": u, "category": c} for c, n, u in SEED])


def downgrade():
    op.drop_index(op.f('ix_repair_bill_items_bill_id'), table_name='repair_bill_items')
    op.drop_table('repair_bill_items')
    op.drop_index(op.f('ix_repair_bill_files_file_id'), table_name='repair_bill_files')
    op.drop_index(op.f('ix_repair_bill_files_bill_id'), table_name='repair_bill_files')
    op.drop_table('repair_bill_files')
    op.drop_index(op.f('ix_request_parts_request_id'), table_name='request_parts')
    op.drop_table('request_parts')
    op.drop_index(op.f('ix_repair_bills_vehicle_id'), table_name='repair_bills')
    op.drop_index(op.f('ix_repair_bills_request_id'), table_name='repair_bills')
    op.drop_index('ix_repair_bills_number', table_name='repair_bills')
    op.drop_index(op.f('ix_repair_bills_legal_entity_id'), table_name='repair_bills')
    op.drop_index(op.f('ix_repair_bills_bill_date'), table_name='repair_bills')
    op.drop_table('repair_bills')
    op.drop_index('uq_parts_catalog_key_active', table_name='parts_catalog', postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_table('parts_catalog')
