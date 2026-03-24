"""
Tests for gather.py internals: _cache_items and run_gather_job error handling.

_cache_items
    Converts a list of dicts into GatherCache ORM rows. Items that cannot be
    JSON-serialised (e.g. contain a set or a custom object) must be silently
    skipped — the gather pipeline should not crash because of one bad item.

run_gather_job
    The coordinator entry point. On failure it must write gather_status='error'
    and gather_error to the profile, then re-raise so the coordinator can mark
    the queue row. The nested DB write in the except handler must not itself
    raise (the "rollback on inner failure" path).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.db import UserProfile, db as _db
from src.gather import _cache_items, run_gather_job
from conftest import TEST_USERNAME


# ── _cache_items ─────────────────────────────────────────────────────────────

class TestCacheItems:
    """
    _cache_items builds GatherCache rows from a list of dicts.

    Key invariant: serialisation failures must be silently swallowed.
    The gather pipeline is best-effort — losing one malformed item is
    preferable to aborting the entire job.
    """

    def test_normal_items_produce_rows(self):
        items = [
            {'icon': '🐍', 'label': 'Python'},
            {'icon': '🦎', 'label': 'Go'},
        ]
        rows = _cache_items(
            user_id=1,
            section='identity',
            item_type='userbox',
            items=items,
            source='wiki',
        )
        assert len(rows) == 2

    def test_rows_have_correct_metadata(self):
        items = [{'icon': '⭐', 'label': 'Star'}]
        rows = _cache_items(
            user_id=7,
            section='identity',
            item_type='userbox',
            items=items,
            source='llm',
        )
        row = rows[0]
        assert row.user_id == 7
        assert row.section == 'identity'
        assert row.item_type == 'userbox'
        assert row.source == 'llm'
        assert row.gathered_at is not None

    def test_unserializable_item_is_skipped(self):
        """
        A set is not JSON-serialisable. The bad item must be silently dropped
        rather than crashing the gather job.
        """
        items = [
            {'ok': 'this is fine'},
            {'bad': {1, 2, 3}},   # sets are not JSON-serialisable
            {'ok': 'also fine'},
        ]
        rows = _cache_items(
            user_id=1,
            section='identity',
            item_type='userbox',
            items=items,
            source='wiki',
        )
        # Only the two serialisable items should produce rows
        assert len(rows) == 2

    def test_all_unserializable_returns_empty_list(self):
        items = [{'bad': object()}, {'also_bad': lambda x: x}]
        rows = _cache_items(
            user_id=1,
            section='identity',
            item_type='userbox',
            items=items,
            source='wiki',
        )
        assert rows == []

    def test_empty_items_returns_empty_list(self):
        rows = _cache_items(
            user_id=1,
            section='identity',
            item_type='userbox',
            items=[],
            source='wiki',
        )
        assert rows == []


# ── run_gather_job error handling ─────────────────────────────────────────────

class TestRunGatherJobErrorHandling:
    """
    run_gather_job must write error state to the profile when _run() raises,
    then re-raise so the coordinator can mark the queue row appropriately.
    """

    def test_exception_sets_gather_status_error(self, flask_app, profile):
        """
        When the inner pipeline raises, gather_status must be set to 'error'
        and gather_error must contain the exception message.
        """
        with flask_app.app_context():
            user = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            user.llm_consent = False
            _db.session.commit()

        with patch('src.gather._run', side_effect=RuntimeError('replica timeout')):
            with pytest.raises(RuntimeError, match='replica timeout'):
                run_gather_job(username=TEST_USERNAME, app=flask_app)

        with flask_app.app_context():
            user = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            assert user.gather_status == 'error'
            assert 'replica timeout' in (user.gather_error or '')

    def test_exception_is_reraised(self, flask_app, profile):
        """
        run_gather_job must re-raise after writing error state — the coordinator
        uses the raised exception to detect failures.
        """
        with patch('src.gather._run', side_effect=ValueError('boom')):
            with pytest.raises(ValueError):
                run_gather_job(username=TEST_USERNAME, app=flask_app)

    def test_gather_error_truncated_to_500_chars(self, flask_app, profile):
        """
        gather_error is a VARCHAR(500) column — messages longer than 500 chars
        must be truncated at the source to avoid DB constraint violations.
        """
        long_message = 'x' * 1000

        with patch('src.gather._run', side_effect=RuntimeError(long_message)):
            with pytest.raises(RuntimeError):
                run_gather_job(username=TEST_USERNAME, app=flask_app)

        with flask_app.app_context():
            user = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            assert len(user.gather_error or '') <= 500

    def test_missing_profile_still_reraises(self, flask_app):
        """
        If the profile does not exist, _run() raises RuntimeError. The error
        handler must not crash on the missing profile, and must still re-raise.
        """
        with patch(
            'src.gather._run',
            side_effect=RuntimeError('No profile found for …'),
        ):
            with pytest.raises(RuntimeError):
                run_gather_job(username='NonExistentUser', app=flask_app)
