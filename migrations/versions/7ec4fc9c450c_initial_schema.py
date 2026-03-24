"""initial schema

Revision ID: 7ec4fc9c450c
Revises:
Create Date: 2026-03-22 18:19:04.955566

Note: UTCDateTime is a TypeDecorator that wraps sa.DateTime. Alembic cannot
resolve it at migration run-time, so all datetime columns use sa.DateTime()
directly here. The runtime behaviour is identical — UTCDateTime adds no extra
DDL; it only adjusts Python-level serialisation for MySQL.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7ec4fc9c450c'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('gather_queue',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('gather_queue', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_gather_queue_username'), ['username'], unique=False)

    op.create_table('snapshot_queue',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('snapshot_queue', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_snapshot_queue_username'), ['username'], unique=False)

    op.create_table('user_profile',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=255), nullable=False),
    sa.Column('registration_date', sa.DateTime(), nullable=True),
    sa.Column('total_edits', sa.Integer(), nullable=True),
    sa.Column('user_rights', sa.Text(), nullable=True),
    sa.Column('avatar_filename', sa.String(length=500), nullable=True),
    sa.Column('home_base', sa.String(length=100), nullable=True),
    sa.Column('biography', sa.Text(), nullable=True),
    sa.Column('biography_image_filename', sa.String(length=500), nullable=True),
    sa.Column('extra_sections', sa.Text(), nullable=True),
    sa.Column('selected_achievements', sa.Text(), nullable=True),
    sa.Column('userboxes', sa.Text(), nullable=True),
    sa.Column('barnstars', sa.Text(), nullable=True),
    sa.Column('signature_html', sa.Text(), nullable=True),
    sa.Column('proud_of', sa.Text(), nullable=True),
    sa.Column('snapshot_at', sa.DateTime(), nullable=True),
    sa.Column('snapshot_version', sa.String(length=30), nullable=True),
    sa.Column('user_approved_snapshot_version', sa.String(length=30), nullable=True),
    sa.Column('processed_at', sa.DateTime(), nullable=True),
    sa.Column('gather_status', sa.String(length=20), nullable=False),
    sa.Column('gather_progress', sa.Integer(), nullable=False),
    sa.Column('llm_consent', sa.Boolean(), nullable=True),
    sa.Column('gather_error', sa.String(length=500), nullable=True),
    sa.Column('last_gathered_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_profile', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_profile_username'), ['username'], unique=True)

    op.create_table('gather_cache',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('section', sa.String(length=20), nullable=False),
    sa.Column('item_type', sa.String(length=30), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('gathered_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user_profile.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('gather_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_gather_cache_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('gather_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_gather_cache_user_id'))

    op.drop_table('gather_cache')
    with op.batch_alter_table('user_profile', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_profile_username'))

    op.drop_table('user_profile')
    with op.batch_alter_table('snapshot_queue', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_snapshot_queue_username'))

    op.drop_table('snapshot_queue')
    with op.batch_alter_table('gather_queue', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_gather_queue_username'))

    op.drop_table('gather_queue')
