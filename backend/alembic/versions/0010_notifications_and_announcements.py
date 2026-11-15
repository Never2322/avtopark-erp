"""Уведомления и объявления

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('announcements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('body', sa.Text(), server_default='', nullable=False),
    sa.Column('audience', sa.String(length=8), server_default='drivers', nullable=False),
    sa.Column('closed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by_id', sa.Integer(), nullable=True),
    sa.Column('archive_reason', sa.String(length=300), server_default='', nullable=False),
    sa.CheckConstraint("audience IN ('drivers', 'all', 'pick')", name=op.f('ck_announcements_audience')),
    sa.CheckConstraint('length(btrim(title)) > 0', name=op.f('ck_announcements_title')),
    sa.ForeignKeyConstraint(['archived_by_id'], ['users.id'], name=op.f('fk_announcements_archived_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_announcements_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_announcements_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_announcements_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_announcements'))
    )
    op.create_index(op.f('ix_announcements_legal_entity_id'), 'announcements', ['legal_entity_id'], unique=False)
    op.create_table('notifications',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=12), server_default='info', nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('text', sa.String(length=500), server_default='', nullable=False),
    sa.Column('url', sa.String(length=200), server_default='', nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=True),
    sa.Column('key', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('request', 'reply', 'announce', 'job', 'parts', 'reminder', 'info')", name=op.f('ck_notifications_kind')),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_notifications_legal_entity_id_legal_entities')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    op.create_index('ix_notifications_user', 'notifications', ['user_id', 'read_at', 'id'], unique=False)
    op.create_index('uq_notifications_user_key', 'notifications', ['user_id', 'key'], unique=True, postgresql_where=sa.text('key IS NOT NULL'))
    op.create_table('announcement_replies',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('announcement_id', sa.Integer(), nullable=False),
    sa.Column('thread_user_id', sa.Integer(), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=True),
    sa.Column('from_staff', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('text', sa.String(length=1000), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(btrim(text)) > 0', name=op.f('ck_announcement_replies_text')),
    sa.ForeignKeyConstraint(['announcement_id'], ['announcements.id'], name=op.f('fk_announcement_replies_announcement_id_announcements'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_announcement_replies_author_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['thread_user_id'], ['users.id'], name=op.f('fk_announcement_replies_thread_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_announcement_replies'))
    )
    op.create_index('ix_announcement_replies_thread', 'announcement_replies', ['announcement_id', 'thread_user_id', 'id'], unique=False)
    op.create_table('announcement_targets',
    sa.Column('announcement_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['announcement_id'], ['announcements.id'], name=op.f('fk_announcement_targets_announcement_id_announcements'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_announcement_targets_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('announcement_id', 'user_id', name=op.f('pk_announcement_targets'))
    )
    op.create_index(op.f('ix_announcement_targets_user_id'), 'announcement_targets', ['user_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_announcement_targets_user_id'), table_name='announcement_targets')
    op.drop_table('announcement_targets')
    op.drop_index('ix_announcement_replies_thread', table_name='announcement_replies')
    op.drop_table('announcement_replies')
    op.drop_index('uq_notifications_user_key', table_name='notifications', postgresql_where=sa.text('key IS NOT NULL'))
    op.drop_index('ix_notifications_user', table_name='notifications')
    op.drop_table('notifications')
    op.drop_index(op.f('ix_announcements_legal_entity_id'), table_name='announcements')
    op.drop_table('announcements')
