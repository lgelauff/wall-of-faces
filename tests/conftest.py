"""
Shared fixtures for Flask integration tests.

All tests that touch routes or the database use the fixtures defined here.

Key design decisions:
  - Each test gets its own fresh SQLite database file (via tmp_path) so tests
    are fully isolated and cannot bleed state into each other.
  - Flask-Session is patched out so the standard cookie session is used instead.
    This keeps tests simple — no DB session table needed, and session values can
    be set directly via client.session_transaction().
  - The background coordinator and stale-job recovery are no-ops in tests.
    They use threads and real DB state; neither is wanted during unit tests.
"""

import pytest
from unittest.mock import patch

import app as app_module
from app import create_app
from src.db import db as _db, GatherQueue, UserProfile

# ── Usernames used across test modules ────────────────────────────────────────

TEST_USERNAME  = 'TestUser'
ADMIN_USERNAME = 'AdminUser'


# ── App fixture ───────────────────────────────────────────────────────────────

@pytest.fixture()
def flask_app(tmp_path):
    """
    A Flask application instance wired to a fresh SQLite database.

    Patches applied for the duration of each test:
      _read_secret    → returns test values instead of reading from disk/env
      start_coordinator → no-op (avoids spawning background threads)
      recover_stale_jobs → no-op (avoids touching DB at startup)
      Session          → no-op (Flask-Session bypassed; standard cookie session used)
    """
    db_path = tmp_path / 'test.db'

    def _secret_stub(name: str) -> str:
        """Return test-safe values for secrets."""
        if name == 'database-url':
            return f'sqlite:///{db_path}'
        if name == 'secret-key':
            return 'test-secret-key-do-not-use-in-production'
        return ''

    with (
        patch('app._read_secret', side_effect=_secret_stub),
        patch('app.start_coordinator'),
        patch('app.recover_stale_jobs'),
        patch('app.Session'),           # bypass Flask-Session, use cookie sessions
    ):
        application = create_app()

    application.config['TESTING'] = True

    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


# ── Client fixtures ───────────────────────────────────────────────────────────

@pytest.fixture()
def client(flask_app):
    """Unauthenticated test client."""
    return flask_app.test_client()


@pytest.fixture()
def profile(flask_app):
    """
    Create a UserProfile for TEST_USERNAME and return the username string.

    Dependent fixtures (auth_client) rely on this fixture to ensure the profile
    row exists in the DB before any route tries to load it.
    """
    with flask_app.app_context():
        user_profile = UserProfile(username=TEST_USERNAME)
        _db.session.add(user_profile)
        _db.session.commit()
    return TEST_USERNAME


@pytest.fixture()
def auth_client(client, profile):
    """
    Test client with a valid session for TEST_USERNAME.

    The profile fixture is a dependency to ensure the DB row exists before
    any route tries to look up the user.
    """
    with client.session_transaction() as sess:
        sess['username'] = TEST_USERNAME
    return client


@pytest.fixture()
def admin_client(client, flask_app, monkeypatch):
    """
    Test client with a valid session for ADMIN_USERNAME.

    ADMIN_USERS is patched at the module level because admin_required()
    reads it as a module-level global, not from app.config.
    """
    monkeypatch.setattr(app_module, 'ADMIN_USERS', [ADMIN_USERNAME])
    flask_app.config['ADMIN_USERS'] = [ADMIN_USERNAME]

    with flask_app.app_context():
        admin_profile = UserProfile(username=ADMIN_USERNAME)
        _db.session.add(admin_profile)
        _db.session.commit()

    with client.session_transaction() as sess:
        sess['username'] = ADMIN_USERNAME

    return client
