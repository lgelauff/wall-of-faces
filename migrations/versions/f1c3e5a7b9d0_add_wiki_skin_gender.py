"""Add wiki_skin and wiki_gender to user_profile.

Revision ID: f1c3e5a7b9d0
Revises: d2f4e6a8c1b0
Create Date: 2026-03-24

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = 'f1c3e5a7b9d0'
down_revision = 'd2f4e6a8c1b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_profile', sa.Column('wiki_skin',   sa.String(50),  nullable=True))
    op.add_column('user_profile', sa.Column('wiki_gender', sa.String(20),  nullable=True))


def downgrade() -> None:
    op.drop_column('user_profile', 'wiki_gender')
    op.drop_column('user_profile', 'wiki_skin')
