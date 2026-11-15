"""stock

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


KINDS_OLD = "'request', 'reply', 'announce', 'job', 'parts', 'reminder', 'info'"
KINDS_NEW = "'request', 'reply', 'announce', 'job', 'parts', 'reminder', 'stock', 'info'"


def upgrade():
    # новый вид уведомления «Склад» (товар заканчивается)
    op.drop_constraint(op.f('ck_notifications_kind'), 'notifications', type_='check')
    op.create_check_constraint(op.f('ck_notifications_kind'), 'notifications', f"kind IN ({KINDS_NEW})")
    op.create_table('stock_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('name_key', sa.String(length=200), nullable=False),
    sa.Column('sku', sa.String(length=60), server_default='', nullable=False),
    sa.Column('unit', sa.String(length=12), server_default='шт', nullable=False),
    sa.Column('min_qty', sa.Numeric(precision=12, scale=3), server_default='0', nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint('min_qty >= 0', name=op.f('ck_stock_items_min_qty')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_stock_items_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_stock_items_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_stock_items_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_stock_items_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_items'))
    )
    op.create_index(op.f('ix_stock_items_legal_entity_id'), 'stock_items', ['legal_entity_id'], unique=False)
    op.create_index('uq_stock_items_key_active', 'stock_items', ['legal_entity_id', 'name_key'], unique=True, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_table('warehouses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint('length(btrim(name)) > 0', name=op.f('ck_warehouses_name')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_warehouses_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_warehouses_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_warehouses_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_warehouses_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_warehouses'))
    )
    op.create_index(op.f('ix_warehouses_legal_entity_id'), 'warehouses', ['legal_entity_id'], unique=False)
    op.create_index('uq_warehouses_name_active', 'warehouses', ['legal_entity_id', sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_table('stock_moves',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('move_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('warehouse_id', sa.Integer(), nullable=False),
    sa.Column('to_warehouse_id', sa.Integer(), nullable=True),
    sa.Column('qty', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('price', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('counterparty_id', sa.Integer(), nullable=True),
    sa.Column('vehicle_id', sa.Integer(), nullable=True),
    sa.Column('request_id', sa.Integer(), nullable=True),
    sa.Column('request_part_id', sa.Integer(), nullable=True),
    sa.Column('bill_id', sa.Integer(), nullable=True),
    sa.Column('doc_number', sa.String(length=60), server_default='', nullable=False),
    sa.Column('note', sa.String(length=300), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint("(kind = 'transfer' AND to_warehouse_id IS NOT NULL AND to_warehouse_id <> warehouse_id) OR (kind <> 'transfer' AND to_warehouse_id IS NULL)", name=op.f('ck_stock_moves_transfer')),
    sa.CheckConstraint("kind IN ('receipt', 'issue', 'writeoff', 'transfer')", name=op.f('ck_stock_moves_kind')),
    sa.CheckConstraint('qty > 0 AND price >= 0', name=op.f('ck_stock_moves_values')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_stock_moves_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['bill_id'], ['repair_bills.id'], name=op.f('fk_stock_moves_bill_id_repair_bills')),
    sa.ForeignKeyConstraint(['counterparty_id'], ['counterparties.id'], name=op.f('fk_stock_moves_counterparty_id_counterparties')),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_stock_moves_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['stock_items.id'], name=op.f('fk_stock_moves_item_id_stock_items')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_stock_moves_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['request_id'], ['service_requests.id'], name=op.f('fk_stock_moves_request_id_service_requests')),
    sa.ForeignKeyConstraint(['request_part_id'], ['request_parts.id'], name=op.f('fk_stock_moves_request_part_id_request_parts')),
    sa.ForeignKeyConstraint(['to_warehouse_id'], ['warehouses.id'], name=op.f('fk_stock_moves_to_warehouse_id_warehouses')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_stock_moves_updated_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_stock_moves_vehicle_id_vehicles')),
    sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], name=op.f('fk_stock_moves_warehouse_id_warehouses')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_moves'))
    )
    op.create_index(op.f('ix_stock_moves_bill_id'), 'stock_moves', ['bill_id'], unique=False)
    op.create_index('ix_stock_moves_item', 'stock_moves', ['item_id', 'warehouse_id'], unique=False)
    op.create_index(op.f('ix_stock_moves_legal_entity_id'), 'stock_moves', ['legal_entity_id'], unique=False)
    op.create_index(op.f('ix_stock_moves_move_date'), 'stock_moves', ['move_date'], unique=False)
    op.create_index(op.f('ix_stock_moves_request_id'), 'stock_moves', ['request_id'], unique=False)
    op.create_index(op.f('ix_stock_moves_vehicle_id'), 'stock_moves', ['vehicle_id'], unique=False)


def downgrade():
    op.execute("DELETE FROM notifications WHERE kind = 'stock'")
    op.drop_constraint(op.f('ck_notifications_kind'), 'notifications', type_='check')
    op.create_check_constraint(op.f('ck_notifications_kind'), 'notifications', f"kind IN ({KINDS_OLD})")
    op.drop_index(op.f('ix_stock_moves_vehicle_id'), table_name='stock_moves')
    op.drop_index(op.f('ix_stock_moves_request_id'), table_name='stock_moves')
    op.drop_index(op.f('ix_stock_moves_move_date'), table_name='stock_moves')
    op.drop_index(op.f('ix_stock_moves_legal_entity_id'), table_name='stock_moves')
    op.drop_index('ix_stock_moves_item', table_name='stock_moves')
    op.drop_index(op.f('ix_stock_moves_bill_id'), table_name='stock_moves')
    op.drop_table('stock_moves')
    op.drop_index('uq_warehouses_name_active', table_name='warehouses', postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_index(op.f('ix_warehouses_legal_entity_id'), table_name='warehouses')
    op.drop_table('warehouses')
    op.drop_index('uq_stock_items_key_active', table_name='stock_items', postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_index(op.f('ix_stock_items_legal_entity_id'), table_name='stock_items')
    op.drop_table('stock_items')
