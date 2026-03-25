"""Convert text columns to utf8mb4 to support 4-byte Unicode (emoji etc.)

Revision ID: c2d4f6a8b0e1
Revises: a3b5c7d9e1f2
Create Date: 2026-03-25

MySQL's 'utf8' charset is actually 3-byte only; emoji and other 4-byte codepoints
cause DataError on insert.  This migration converts the affected tables and their
TEXT/VARCHAR columns to utf8mb4 / utf8mb4_unicode_ci.

The immediate workaround (ensure_ascii=True in gather.py _cache_items) stays in
place — it prevents breakage on servers that haven't run this migration yet.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers
revision = 'c2d4f6a8b0e1'
down_revision = 'a3b5c7d9e1f2'
branch_labels = None
depends_on = None


# Columns to convert per table.
# Only TEXT/VARCHAR columns that may receive user-generated content with emoji.
_CONVERSIONS: list[tuple[str, str]] = [
    # (table, column)
    # gather_cache
    ('gather_cache',  'payload'),
    ('gather_cache',  'source'),
    # user_profile — all Text columns that store user content or JSON blobs
    ('user_profile',  'biography'),
    ('user_profile',  'extra_sections'),
    ('user_profile',  'userboxes'),
    ('user_profile',  'barnstars'),
    ('user_profile',  'signature_html'),
    ('user_profile',  'proud_of'),
    ('user_profile',  'user_rights'),
]


def upgrade() -> None:
    conn = op.get_bind()

    # Convert each column individually — this avoids locking the full table for
    # a single CONVERT TO CHARACTER SET statement, and lets the migration be
    # applied incrementally if needed.
    for table, column in _CONVERSIONS:
        # Determine current column type so we keep the same DDL type.
        # We use MODIFY COLUMN with explicit type rather than ALTER ... CONVERT TO
        # to avoid touching unrelated columns.
        result = conn.execute(
            sa.text(
                "SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "  AND TABLE_NAME = :t AND COLUMN_NAME = :c"
            ),
            {'t': table, 'c': column},
        ).fetchone()
        if result is None:
            continue  # column doesn't exist (shouldn't happen, but be safe)

        col_type = result[0]  # e.g. "text", "varchar(500)", etc.

        conn.execute(sa.text(
            f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` {col_type} "
            f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    for table, column in _CONVERSIONS:
        result = conn.execute(
            sa.text(
                "SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "  AND TABLE_NAME = :t AND COLUMN_NAME = :c"
            ),
            {'t': table, 'c': column},
        ).fetchone()
        if result is None:
            continue
        col_type = result[0]
        conn.execute(sa.text(
            f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` {col_type} "
            f"CHARACTER SET utf8 COLLATE utf8_general_ci"
        ))


