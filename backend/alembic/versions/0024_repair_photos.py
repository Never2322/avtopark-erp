"""repair_photos

«Фото ремонта»: результат ремонта (что сделано, дата, техника), фото к нему и подтверждение директора.
Права repair_photos.view / create / edit / void и repair_photos.confirm.

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None

_CODES = ("repair_photos.view", "repair_photos.create", "repair_photos.edit", "repair_photos.void", "repair_photos.confirm")


def upgrade():
    op.create_table('repair_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('vehicle_id', sa.Integer(), nullable=False),
    sa.Column('request_id', sa.Integer(), nullable=True),
    sa.Column('repair_date', sa.Date(), nullable=False),
    sa.Column('what', sa.Text(), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_repair_results_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name=op.f('fk_repair_results_vehicle_id_vehicles')),
    sa.ForeignKeyConstraint(['request_id'], ['service_requests.id'], name=op.f('fk_repair_results_request_id_service_requests'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['confirmed_by_id'], ['users.id'], name=op.f('fk_repair_results_confirmed_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_repair_results_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_repair_results_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_repair_results_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_repair_results'))
    )
    op.create_index(op.f('ix_repair_results_legal_entity_id'), 'repair_results', ['legal_entity_id'], unique=False)
    op.create_index(op.f('ix_repair_results_vehicle_id'), 'repair_results', ['vehicle_id'], unique=False)
    op.create_index(op.f('ix_repair_results_request_id'), 'repair_results', ['request_id'], unique=False)
    op.create_index('ix_repair_results_date', 'repair_results', ['repair_date'], unique=False)

    op.create_table('repair_result_photos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('result_id', sa.Integer(), nullable=False),
    sa.Column('file_id', sa.BigInteger(), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('added_by_id', sa.Integer(), nullable=True),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('removed_by_id', sa.Integer(), nullable=True),
    sa.Column('remove_reason', sa.String(length=300), server_default='', nullable=False),
    sa.ForeignKeyConstraint(['result_id'], ['repair_results.id'], name=op.f('fk_repair_result_photos_result_id_repair_results')),
    sa.ForeignKeyConstraint(['file_id'], ['stored_files.id'], name=op.f('fk_repair_result_photos_file_id_stored_files')),
    sa.ForeignKeyConstraint(['added_by_id'], ['users.id'], name=op.f('fk_repair_result_photos_added_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['removed_by_id'], ['users.id'], name=op.f('fk_repair_result_photos_removed_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_repair_result_photos'))
    )
    op.create_index(op.f('ix_repair_result_photos_result_id'), 'repair_result_photos', ['result_id'], unique=False)
    op.create_index(op.f('ix_repair_result_photos_file_id'), 'repair_result_photos', ['file_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_repair_result_photos_file_id'), table_name='repair_result_photos')
    op.drop_index(op.f('ix_repair_result_photos_result_id'), table_name='repair_result_photos')
    op.drop_table('repair_result_photos')
    op.drop_index('ix_repair_results_date', table_name='repair_results')
    op.drop_index(op.f('ix_repair_results_request_id'), table_name='repair_results')
    op.drop_index(op.f('ix_repair_results_vehicle_id'), table_name='repair_results')
    op.drop_index(op.f('ix_repair_results_legal_entity_id'), table_name='repair_results')
    op.drop_table('repair_results')
    codes = ", ".join("'%s'" % c for c in _CODES)
    op.execute(f"DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({codes}))")
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
