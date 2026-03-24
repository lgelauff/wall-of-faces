"""
Tests for POST /api/gather in app.py.

The gather endpoint enforces several gates before enqueuing a job:
  1. LLM consent must be provided on first gather (profile.llm_consent is None).
  2. A cooldown window must have elapsed since the last gather.
  3. The global queue must not be at capacity.
  4. An already-queued or running job short-circuits with a 200.
  5. Consent revocation immediately deletes cached LLM rows.
"""

import pytest
from src.db import db as _db, GatherCache, GatherQueue, UserProfile
from src.utils import utcnow
from conftest import TEST_USERNAME


def _get_profile(flask_app) -> UserProfile:
    """Helper: fetch the test profile within the current app context."""
    return UserProfile.query.filter_by(username=TEST_USERNAME).first()


class TestGatherConsent:
    """LLM consent must be collected on the first gather attempt."""

    def test_first_gather_without_consent_returns_400(self, auth_client):
        """
        profile.llm_consent is NULL and the request omits llm_consent →
        the server must ask for consent before doing anything.
        """
        response = auth_client.post('/api/gather', json={})

        assert response.status_code == 400
        assert response.get_json()['error'] == 'llm_consent_required'

    def test_gather_with_consent_true_enqueues(self, auth_client):
        """Providing llm_consent=True on first gather should succeed."""
        response = auth_client.post('/api/gather', json={'llm_consent': True})

        assert response.status_code == 200
        assert response.get_json()['status'] == 'queued'

    def test_gather_with_consent_false_enqueues(self, auth_client):
        """Declining LLM consent is a valid choice and should still enqueue."""
        response = auth_client.post('/api/gather', json={'llm_consent': False})

        assert response.status_code == 200
        assert response.get_json()['status'] == 'queued'

    def test_second_gather_without_consent_allowed_when_already_set(
        self, auth_client, flask_app
    ):
        """
        Once consent is on file (not NULL), subsequent gathers may omit
        llm_consent from the request body.
        """
        with flask_app.app_context():
            profile = _get_profile(flask_app)
            profile.llm_consent = False   # previously set
            _db.session.commit()

        response = auth_client.post('/api/gather', json={})

        # Should not return 400 for missing consent
        assert response.status_code != 400


class TestGatherCooldown:
    """A gather cannot be triggered again within GATHER_COOLDOWN_SECONDS."""

    def test_gather_within_cooldown_returns_429(self, auth_client, flask_app):
        with flask_app.app_context():
            profile = _get_profile(flask_app)
            profile.llm_consent = False
            # Set last_gathered_at to now so the cooldown window is still open
            profile.last_gathered_at = utcnow()
            _db.session.commit()

        response = auth_client.post('/api/gather', json={})

        assert response.status_code == 429
        data = response.get_json()
        assert data['error'] == 'too_soon'
        assert 'retry_after_seconds' in data


class TestGatherQueueState:
    """In-flight jobs and a full queue must be handled gracefully."""

    def test_already_queued_returns_200_with_status(self, auth_client, flask_app):
        """
        If the user's job is already in the queue, the endpoint should return
        200 with the current status rather than enqueuing a duplicate.
        """
        with flask_app.app_context():
            profile = _get_profile(flask_app)
            profile.llm_consent = False
            profile.gather_status = 'queued'
            _db.session.add(GatherQueue(username=TEST_USERNAME))
            _db.session.commit()

        response = auth_client.post('/api/gather', json={})

        assert response.status_code == 200
        assert response.get_json()['status'] == 'queued'

    def test_full_queue_returns_503(self, auth_client, flask_app):
        """
        When the global queue is at MAX_QUEUE_DEPTH, new jobs must be rejected
        to avoid unbounded memory/resource growth.
        """
        max_depth = flask_app.config['MAX_QUEUE_DEPTH']

        with flask_app.app_context():
            profile = _get_profile(flask_app)
            profile.llm_consent = False
            for i in range(max_depth):
                _db.session.add(GatherQueue(username=f'other_user_{i}'))
            _db.session.commit()

        response = auth_client.post('/api/gather', json={})

        assert response.status_code == 503
        assert response.get_json()['error'] == 'queue_full'

    def test_successful_enqueue_sets_gather_status(self, auth_client, flask_app):
        """After a successful enqueue the profile's gather_status must be 'queued'."""
        auth_client.post('/api/gather', json={'llm_consent': False})

        with flask_app.app_context():
            profile = _get_profile(flask_app)
            assert profile.gather_status == 'queued'


class TestGatherConsentRevocation:
    """Revoking LLM consent must immediately delete cached LLM rows."""

    def test_revoke_consent_deletes_llm_cache_rows(self, auth_client, flask_app):
        """
        When a user switches from llm_consent=True to llm_consent=False,
        any GatherCache rows from the LLM source must be removed at once
        (GDPR: the user withdrawing consent should stop any processing).
        """
        with flask_app.app_context():
            profile = _get_profile(flask_app)
            profile.llm_consent = True
            # Simulate existing LLM cache rows
            _db.session.add(GatherCache(
                user_id=profile.id,
                section='identity',
                item_type='userbox',
                payload='{"icon":"🐍","label":"Python"}',
                source='llm',
                gathered_at=utcnow(),
            ))
            _db.session.commit()

        # Revoke consent
        auth_client.post('/api/gather', json={'llm_consent': False})

        with flask_app.app_context():
            profile = _get_profile(flask_app)
            llm_rows = GatherCache.query.filter_by(
                user_id=profile.id,
                source='llm',
            ).count()
            assert llm_rows == 0
