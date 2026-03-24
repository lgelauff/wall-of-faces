"""add content_image_filenames to user_profile

Revision ID: d2f4e6a8c1b0
Revises: b8e2f1a3c5d7
Create Date: 2026-03-24 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd2f4e6a8c1b0'
down_revision = 'b8e2f1a3c5d7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_profile', schema=None) as batch_op:
        batch_op.add_column(sa.Column('content_image_filenames', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('user_profile', schema=None) as batch_op:
        batch_op.drop_column('content_image_filenames')
