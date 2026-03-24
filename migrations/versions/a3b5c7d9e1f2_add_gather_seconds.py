"""Add gather_seconds to user_profile.

Revision ID: a3b5c7d9e1f2
Revises: f1c3e5a7b9d0
Create Date: 2026-03-24

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = 'a3b5c7d9e1f2'
down_revision = 'f1c3e5a7b9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_profile', sa.Column('gather_seconds', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('user_profile', 'gather_seconds')
