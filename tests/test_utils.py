"""Tests for src/utils.py — utcnow() and UTCDateTime TypeDecorator."""
from datetime import datetime, timezone, timedelta
import pytest

from src.utils import utcnow, UTCDateTime


class TestUtcnow:
    def test_returns_datetime(self):
        result = utcnow()
        assert isinstance(result, datetime)

    def test_is_timezone_aware(self):
        result = utcnow()
        assert result.tzinfo is not None

    def test_is_utc(self):
        result = utcnow()
        assert result.tzinfo == timezone.utc

    def test_is_recent(self):
        before = datetime.now(timezone.utc)
        result = utcnow()
        after = datetime.now(timezone.utc)
        assert before <= result <= after


class TestUTCDateTime:
    """
    TypeDecorator methods are called by SQLAlchemy with (value, dialect).
    We pass None as dialect since process_bind_param / process_result_value
    don't use it.
    """

    def setup_method(self):
        self.col = UTCDateTime()

    # ── process_bind_param (write path: Python → DB) ──────────────────────────

    def test_bind_strips_tzinfo_from_aware_datetime(self):
        dt = datetime(2020, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        result = self.col.process_bind_param(dt, None)
        assert result.tzinfo is None
        assert result == datetime(2020, 3, 14, 12, 0, 0)

    def test_bind_passes_naive_datetime_unchanged(self):
        dt = datetime(2020, 3, 14, 12, 0, 0)
        result = self.col.process_bind_param(dt, None)
        assert result == dt
        assert result.tzinfo is None

    def test_bind_none_returns_none(self):
        assert self.col.process_bind_param(None, None) is None

    def test_bind_preserves_microseconds(self):
        dt = datetime(2020, 3, 14, 12, 0, 0, 123456, tzinfo=timezone.utc)
        result = self.col.process_bind_param(dt, None)
        assert result.microsecond == 123456

    def test_bind_non_utc_aware_datetime_strips_tzinfo(self):
        from datetime import timezone as tz
        eastern = tz(timedelta(hours=-5))
        dt = datetime(2020, 3, 14, 7, 0, 0, tzinfo=eastern)
        result = self.col.process_bind_param(dt, None)
        # Strips tzinfo, does not convert — the caller is responsible for UTC
        assert result.tzinfo is None

    # ── process_result_value (read path: DB → Python) ─────────────────────────

    def test_result_attaches_utc_to_naive_datetime(self):
        dt = datetime(2020, 3, 14, 12, 0, 0)
        result = self.col.process_result_value(dt, None)
        assert result.tzinfo == timezone.utc
        assert result == datetime(2020, 3, 14, 12, 0, 0, tzinfo=timezone.utc)

    def test_result_none_returns_none(self):
        assert self.col.process_result_value(None, None) is None

    def test_result_preserves_microseconds(self):
        dt = datetime(2020, 3, 14, 12, 0, 0, 999999)
        result = self.col.process_result_value(dt, None)
        assert result.microsecond == 999999

    def test_result_already_aware_datetime_replaces_tzinfo(self):
        # Should not happen from MySQL, but handle gracefully
        from datetime import timezone as tz
        dt = datetime(2020, 3, 14, 12, 0, 0, tzinfo=tz(timedelta(hours=1)))
        result = self.col.process_result_value(dt, None)
        # replace() changes tzinfo regardless of existing value
        assert result.tzinfo == timezone.utc

    # ── Round-trip ────────────────────────────────────────────────────────────

    def test_roundtrip_aware_datetime(self):
        original = datetime(2026, 5, 1, 14, 30, 22, 412381, tzinfo=timezone.utc)
        stored   = self.col.process_bind_param(original, None)
        restored = self.col.process_result_value(stored, None)
        assert restored == original
        assert restored.tzinfo == timezone.utc

    def test_cache_ok_is_true(self):
        assert UTCDateTime.cache_ok is True
