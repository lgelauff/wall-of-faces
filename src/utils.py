"""
utils.py — shared helpers used across all modules.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime as SADateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """
    SQLAlchemy TypeDecorator for UTC datetimes on MySQL.

    MySQL DATETIME does not store timezone info.  SQLAlchemy's
    DateTime(timezone=True) on MySQL is a silent no-op — it stores and
    retrieves naive datetimes, causing TypeError when you compare them
    against timezone-aware values.

    This TypeDecorator:
      - On write: strips tzinfo so MySQL receives a plain naive value.
      - On read:  re-attaches timezone.utc so the application always
                  works with aware datetimes.

    Usage:
        class MyModel(db.Model):
            created_at = db.Column(UTCDateTime, default=utcnow)
    """

    impl = SADateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        """Strip tzinfo before storing so MySQL receives a naive DATETIME.

        Args:
            value: The datetime to store, possibly timezone-aware.
            dialect: SQLAlchemy dialect (unused; required by interface).

        Returns:
            A naive datetime, or None.
        """
        if value is not None and value.tzinfo is not None:
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        """Re-attach UTC tzinfo when reading back from MySQL.

        Args:
            value: The naive datetime read from the database.
            dialect: SQLAlchemy dialect (unused; required by interface).

        Returns:
            A timezone-aware (UTC) datetime, or None.
        """
        if value is not None:
            return value.replace(tzinfo=timezone.utc)
        return value
