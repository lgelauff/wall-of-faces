"""
Tests for POST /api/save-profile in app.py.

This is the primary user data-ingestion endpoint. It accepts a JSON body,
validates each field individually, and writes to UserProfile. Malformed or
oversized input must be rejected before reaching the DB or the render layer.

Validation rules under test:
  home_base            — max 100 chars
  biography            — max 2000 chars
  userboxes            — must be list, max 20 items
  barnstars            — must be list, max 2 items
  selected_achievements — must be list (no size cap)
  proud_of             — must be list, max 10 items
  extra_sections       — must be list; each title ≤100, text ≤500
"""

import json

import pytest
from src.db import UserProfile, db as _db


class TestSaveProfileSuccess:
    """Valid payloads should be persisted and return saved:true."""

    def test_save_home_base(self, auth_client):
        response = auth_client.post(
            '/api/save-profile',
            json={'home_base': 'Amsterdam'},
        )

        assert response.status_code == 200
        assert response.get_json()['saved'] is True

    def test_save_biography(self, auth_client):
        response = auth_client.post(
            '/api/save-profile',
            json={'biography': 'I edit Wikipedia.'},
        )

        assert response.status_code == 200

    def test_save_userboxes_list(self, auth_client):
        userboxes = [{'icon': '🐍', 'label': 'Python', 'bg': '', 'fg': ''}]
        response = auth_client.post(
            '/api/save-profile',
            json={'userboxes': userboxes},
        )

        assert response.status_code == 200

    def test_save_selected_achievements(self, auth_client):
        response = auth_client.post(
            '/api/save-profile',
            json={'selected_achievements': ['veteran', 'prolific']},
        )

        assert response.status_code == 200

    def test_response_includes_updated_at(self, auth_client):
        response = auth_client.post(
            '/api/save-profile',
            json={'home_base': 'Utrecht'},
        )

        data = response.get_json()
        assert 'updated_at' in data

    def test_null_field_clears_value(self, auth_client, flask_app):
        """Sending null for a field should clear it in the DB."""
        # First set a value
        auth_client.post('/api/save-profile', json={'home_base': 'Rotterdam'})
        # Then clear it
        auth_client.post('/api/save-profile', json={'home_base': None})

        with flask_app.app_context():
            from conftest import TEST_USERNAME
            profile = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            assert profile.home_base is None

    def test_empty_body_returns_400(self, auth_client):
        """A request with no JSON body at all must be rejected."""
        response = auth_client.post(
            '/api/save-profile',
            content_type='application/json',
            data='',
        )

        assert response.status_code == 400
        assert response.get_json()['error'] == 'invalid_json'


class TestSaveProfileValidation:
    """Out-of-range or wrong-type values must return 422 with field info."""

    def test_home_base_too_long_returns_422(self, auth_client):
        response = auth_client.post(
            '/api/save-profile',
            json={'home_base': 'A' * 101},
        )

        assert response.status_code == 422
        data = response.get_json()
        assert data['error'] == 'validation_error'
        assert data['field'] == 'home_base'

    def test_biography_too_long_returns_422(self, auth_client):
        response = auth_client.post(
            '/api/save-profile',
            json={'biography': 'x' * 2001},
        )

        assert response.status_code == 422
        assert response.get_json()['field'] == 'biography'

    def test_userboxes_not_a_list_returns_422(self, auth_client):
        response = auth_client.post(
            '/api/save-profile',
            json={'userboxes': 'not-a-list'},
        )

        assert response.status_code == 422
        assert response.get_json()['field'] == 'userboxes'

    def test_userboxes_exceeds_20_items_returns_422(self, auth_client):
        too_many = [{'icon': '', 'label': f'item {i}', 'bg': '', 'fg': ''} for i in range(21)]
        response = auth_client.post(
            '/api/save-profile',
            json={'userboxes': too_many},
        )

        assert response.status_code == 422
        assert response.get_json()['field'] == 'userboxes'

    def test_barnstars_exceeds_2_items_returns_422(self, auth_client):
        too_many = [{'filename': f'star{i}.png', 'name': f'Star {i}'} for i in range(3)]
        response = auth_client.post(
            '/api/save-profile',
            json={'barnstars': too_many},
        )

        assert response.status_code == 422
        assert response.get_json()['field'] == 'barnstars'

    def test_proud_of_exceeds_10_items_returns_422(self, auth_client):
        too_many = [{'title': f'Article {i}', 'filename': None} for i in range(11)]
        response = auth_client.post(
            '/api/save-profile',
            json={'proud_of': too_many},
        )

        assert response.status_code == 422
        assert response.get_json()['field'] == 'proud_of'

    def test_extra_section_title_too_long_returns_422(self, auth_client):
        sections = [{'title': 'T' * 101, 'text': 'fine'}]
        response = auth_client.post(
            '/api/save-profile',
            json={'extra_sections': sections},
        )

        assert response.status_code == 422
        assert 'extra_sections' in response.get_json()['field']

    def test_extra_section_text_too_long_returns_422(self, auth_client):
        sections = [{'title': 'Fine title', 'text': 'x' * 501}]
        response = auth_client.post(
            '/api/save-profile',
            json={'extra_sections': sections},
        )

        assert response.status_code == 422

    def test_unauthenticated_returns_401(self, client):
        response = client.post('/api/save-profile', json={'home_base': 'x'})

        assert response.status_code == 401
