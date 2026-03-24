"""
Miscellaneous app.py tests that don't belong to a single endpoint:

  - Security headers: every response must carry the expected HTTP security headers.
  - /api/delete-account: GDPR-critical; must remove the profile and clear the session.
  - app.sanitise_signature: the stricter variant used at OAuth time.
    Unlike gather.sanitise_signature, this one must strip span[style] to prevent
    CSS injection (see security review note at app.py:71).
"""

import pytest
from src.db import db as _db, UserProfile
from conftest import TEST_USERNAME


# ── Security headers ──────────────────────────────────────────────────────────

class TestSecurityHeaders:
    """
    Every HTTP response must include security headers that protect against
    common web vulnerabilities (XSS, clickjacking, MIME sniffing).
    """

    def test_content_security_policy_present(self, client):
        response = client.get('/')
        assert 'Content-Security-Policy' in response.headers

    def test_x_content_type_options_nosniff(self, client):
        response = client.get('/')
        assert response.headers.get('X-Content-Type-Options') == 'nosniff'

    def test_x_frame_options_deny(self, client):
        response = client.get('/')
        assert response.headers.get('X-Frame-Options') == 'DENY'

    def test_security_headers_present_on_api_responses(self, auth_client):
        """Security headers must apply to API endpoints too, not just page routes."""
        response = auth_client.get('/api/gather-status')
        assert 'X-Content-Type-Options' in response.headers

    def test_csp_restricts_scripts_to_self(self, client):
        """CSP must not allow inline scripts or external script sources."""
        csp = client.get('/').headers.get('Content-Security-Policy', '')
        assert "script-src 'self'" in csp

    def test_csp_allows_wikimedia_images(self, client):
        """CSP must permit loading images from upload.wikimedia.org for avatars."""
        csp = client.get('/').headers.get('Content-Security-Policy', '')
        assert 'upload.wikimedia.org' in csp


# ── Delete account ────────────────────────────────────────────────────────────

class TestDeleteAccount:
    """
    /api/delete-account is a GDPR-critical endpoint: it must permanently remove
    all data associated with the user and invalidate their session.
    """

    def test_delete_removes_profile_from_db(self, auth_client, flask_app):
        auth_client.post('/api/delete-account')

        with flask_app.app_context():
            profile = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            assert profile is None

    def test_delete_returns_deleted_true(self, auth_client):
        response = auth_client.post('/api/delete-account')

        assert response.status_code == 200
        assert response.get_json()['deleted'] is True

    def test_delete_clears_session(self, auth_client):
        """
        After deleting the account, the session must be cleared so the user
        is no longer authenticated. Subsequent requests should be treated as
        anonymous.
        """
        auth_client.post('/api/delete-account')

        # The session should now be empty — a protected page must redirect
        response = auth_client.get('/gather')
        assert response.status_code == 302

    def test_unauthenticated_delete_returns_401(self, client):
        response = client.post('/api/delete-account')
        assert response.status_code == 401


# ── app.sanitise_signature ────────────────────────────────────────────────────

class TestAppSanitiseSignature:
    """
    app.py defines its own sanitise_signature that is STRICTER than the one in
    gather.py. The key difference: span[style] is intentionally omitted here
    (see security review N-H6 at app.py:71) to prevent CSS injection in
    signatures shown on printed cards.

    These tests document and verify that intentional difference.
    """

    @pytest.fixture()
    def sanitise(self, flask_app):
        """Import the app-level sanitise_signature within an app context."""
        from app import sanitise_signature
        return sanitise_signature

    def test_span_style_is_stripped(self, sanitise):
        """
        This is the critical difference from gather.sanitise_signature:
        span[style] must be removed to prevent CSS injection on printed cards.
        """
        result = sanitise('<span style="color:red">text</span>')

        # The style attribute must be gone; the text content must survive
        assert 'color:red' not in result
        assert 'text' in result

    def test_script_tag_removed(self, sanitise):
        result = sanitise('<script>alert(1)</script>safe')

        assert '<script>' not in result
        assert 'safe' in result

    def test_allowed_tags_pass_through(self, sanitise):
        """a, b, i, span (without style), sup, sub, abbr must be kept."""
        html = '<b>bold</b> <i>italic</i> <sup>1</sup>'
        result = sanitise(html)

        assert '<b>' in result
        assert '<i>' in result
        assert '<sup>' in result

    def test_href_on_anchor_preserved(self, sanitise):
        result = sanitise('<a href="/wiki/Foo" title="Foo">link</a>')

        assert 'href="/wiki/Foo"' in result

    def test_javascript_href_stripped(self, sanitise):
        result = sanitise('<a href="javascript:evil()">x</a>')

        assert 'javascript:' not in result

    def test_empty_string_returns_empty(self, sanitise):
        assert sanitise('') == ''
