"""add home_wiki to user_profile

Revision ID: b8e2f1a3c5d7
Revises: 7ec4fc9c450c
Create Date: 2026-03-24 09:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b8e2f1a3c5d7'
down_revision = '7ec4fc9c450c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_profile', schema=None) as batch_op:
        batch_op.add_column(sa.Column('home_wiki', sa.String(length=50), nullable=True))


def downgrade():
    with op.batch_alter_table('user_profile', schema=None) as batch_op:
        batch_op.drop_column('home_wiki')
