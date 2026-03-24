"""
Tests for POST /api/finalize in app.py.

The finalize endpoint creates a versioned PDF snapshot of the user's card.
It enforces:
  - A per-user cooldown between finalizations.
  - A maximum number of stored snapshot versions, with a confirm-to-prune
    dialog flow when the cap is reached.
  - Card overflow: WeasyPrint emitting more than one A5 page → 422.

_do_snapshot is patched in all tests so no actual files are written.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from src.db import db as _db, UserProfile
from src.render import CardOverflowError
from src.utils import utcnow
from conftest import TEST_USERNAME

# A realistic-looking snapshot version string for mocking
_FAKE_VERSION   = '20260501_120000_000001'
_FAKE_SNAP_TIME = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _get_profile(flask_app) -> UserProfile:
    return UserProfile.query.filter_by(username=TEST_USERNAME).first()


class TestFinalizeCooldown:
    """A snapshot cannot be created again within FINALIZE_COOLDOWN_SECONDS."""

    def test_finalize_within_cooldown_returns_429(self, auth_client, flask_app):
        with flask_app.app_context():
            profile = _get_profile(flask_app)
            # Mark a very recent snapshot to trigger the cooldown
            profile.snapshot_at = utcnow()
            _db.session.commit()

        response = auth_client.post('/api/finalize', json={})

        assert response.status_code == 429
        data = response.get_json()
        assert data['error'] == 'too_soon'
        assert 'retry_after_seconds' in data


class TestFinalizeSuccess:
    """A clean finalize should return the new version string and timestamp."""

    def test_successful_finalize_returns_version(self, auth_client):
        with patch('app._do_snapshot', return_value=(_FAKE_VERSION, _FAKE_SNAP_TIME)):
            response = auth_client.post('/api/finalize', json={})

        assert response.status_code == 200
        data = response.get_json()
        assert data['snapshot_version'] == _FAKE_VERSION
        assert '2026' in data['snapshot_at']

    def test_unauthenticated_returns_401(self, client):
        response = client.post('/api/finalize', json={})

        assert response.status_code == 401


class TestFinalizeCardOverflow:
    """If the PDF overflows one A5 page, the endpoint must report it clearly."""

    def test_card_overflow_returns_422(self, auth_client):
        overflow_msg = 'Card content exceeds A5 — PDF has 2 pages.'
        with patch('app._do_snapshot', side_effect=CardOverflowError(overflow_msg)):
            response = auth_client.post('/api/finalize', json={})

        assert response.status_code == 422
        data = response.get_json()
        assert data['error'] == 'card_overflow'
        assert overflow_msg in data['detail']


class TestFinalizeVersionCap:
    """When the snapshot version cap is reached, a two-step confirm flow is used."""

    def test_version_cap_returns_409_without_confirm(self, auth_client, flask_app):
        """
        Reaching the snapshot cap should return 409 with the details needed
        to show the user a confirmation dialog before pruning occurs.
        """
        cap = flask_app.config['SNAPSHOT_MAX_VERSIONS_PER_USER']

        with flask_app.app_context():
            profile = _get_profile(flask_app)
            profile.snapshot_version = _FAKE_VERSION
            _db.session.commit()

        # Simulate the cap being reached without touching the filesystem
        with (
            patch('app._count_snapshot_versions', return_value=cap),
            patch('app._do_snapshot', return_value=(_FAKE_VERSION, _FAKE_SNAP_TIME)),
        ):
            response = auth_client.post('/api/finalize', json={})

        assert response.status_code == 409
        data = response.get_json()
        assert data['error'] == 'snapshot_limit_reached'
        assert data['version_count'] == cap
        assert data['keep_version'] == _FAKE_VERSION

    def test_confirm_prune_with_correct_version_succeeds(self, auth_client, flask_app):
        """
        Sending confirm_prune=True with the correct keep_version should
        trigger the prune and proceed to create the new snapshot.
        """
        cap = flask_app.config['SNAPSHOT_MAX_VERSIONS_PER_USER']

        with flask_app.app_context():
            profile = _get_profile(flask_app)
            profile.snapshot_version = _FAKE_VERSION
            _db.session.commit()

        with (
            patch('app._count_snapshot_versions', return_value=cap),
            patch('app._prune_all_except'),
            patch('app._do_snapshot', return_value=(_FAKE_VERSION, _FAKE_SNAP_TIME)),
        ):
            response = auth_client.post('/api/finalize', json={
                'confirm_prune': True,
                'keep_version':  _FAKE_VERSION,
            })

        assert response.status_code == 200

    def test_confirm_prune_with_wrong_version_returns_409(self, auth_client, flask_app):
        """
        If keep_version doesn't match the current snapshot_version, the card
        changed since the dialog was shown — the user must confirm again.
        """
        cap = flask_app.config['SNAPSHOT_MAX_VERSIONS_PER_USER']

        with flask_app.app_context():
            profile = _get_profile(flask_app)
            profile.snapshot_version = _FAKE_VERSION
            _db.session.commit()

        with patch('app._count_snapshot_versions', return_value=cap):
            response = auth_client.post('/api/finalize', json={
                'confirm_prune': True,
                'keep_version':  'stale_version_from_old_dialog',
            })

        assert response.status_code == 409
