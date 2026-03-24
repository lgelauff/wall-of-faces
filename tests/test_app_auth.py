"""
Tests for authentication and authorisation enforcement in app.py.

Covers:
  - login_required: unauthenticated API requests get 401 JSON;
                    unauthenticated page requests redirect to /login.
  - admin_required: non-admin users get 403; admin users pass through.
  - /healthz: available without authentication.
"""

import pytest
from conftest import ADMIN_USERNAME, TEST_USERNAME


class TestLoginRequired:
    """login_required should block anonymous access consistently."""

    def test_unauthenticated_api_request_returns_401_json(self, client):
        """API endpoints must return JSON 401, not an HTML redirect."""
        response = client.get('/api/gather-status')

        assert response.status_code == 401
        data = response.get_json()
        assert data['error'] == 'not_authenticated'

    def test_unauthenticated_page_redirects_to_login(self, client):
        """Page routes should redirect anonymous visitors to /login."""
        response = client.get('/gather')

        assert response.status_code == 302
        assert '/login' in response.headers['Location']

    def test_unauthenticated_buffet_redirects(self, client):
        response = client.get('/buffet')

        assert response.status_code == 302
        assert '/login' in response.headers['Location']

    def test_unauthenticated_card_redirects(self, client):
        response = client.get('/card')

        assert response.status_code == 302

    def test_unauthenticated_save_profile_returns_401(self, client):
        response = client.post('/api/save-profile', json={})

        assert response.status_code == 401
        assert response.get_json()['error'] == 'not_authenticated'

    def test_unauthenticated_finalize_returns_401(self, client):
        response = client.post('/api/finalize', json={})

        assert response.status_code == 401

    def test_authenticated_gather_page_is_accessible(self, auth_client):
        """Sanity check: a logged-in user can reach the gather page."""
        response = auth_client.get('/gather')

        assert response.status_code == 200

    def test_privacy_page_accessible_without_login(self, client):
        """Privacy page should be publicly accessible (no login_required)."""
        response = client.get('/privacy')

        assert response.status_code == 200


class TestAdminRequired:
    """admin_required should block non-admin users with 403."""

    def test_non_admin_user_gets_403(self, auth_client):
        """A regular logged-in user must not access /admin."""
        response = auth_client.get('/admin')

        assert response.status_code == 403

    def test_admin_user_can_access_admin_page(self, admin_client):
        """A user in ADMIN_USERS must be able to access /admin."""
        response = admin_client.get('/admin')

        assert response.status_code == 200

    def test_non_admin_snapshot_post_returns_403(self, auth_client):
        response = auth_client.post('/admin/snapshot', json={})

        assert response.status_code == 403


class TestHealthz:
    """/healthz must respond without authentication."""

    def test_healthz_returns_200_when_db_is_reachable(self, client):
        response = client.get('/healthz')

        assert response.status_code == 200
        assert response.get_json()['ok'] is True
