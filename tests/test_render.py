"""
Tests for src/render.py — pure helper functions (no Flask context needed).

Coverage added beyond the original file:
  - _image_url: file:// path construction, None guards, missing-file behaviour.
  - build_card_context: requires a Flask app context; exercises JSON parsing,
    None-field handling, and SimpleNamespace construction.
"""

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.render import _format_date, _format_edit_count, _image_url


class TestFormatDate:
    def _dt(self, year, month, day):
        return datetime(year, month, day, tzinfo=timezone.utc)

    def test_nl_format(self):
        assert _format_date(self._dt(2012, 3, 14), "nl") == "14\u00a0maart\u00a02012"

    def test_en_format(self):
        assert _format_date(self._dt(2012, 3, 14), "en") == "14\u00a0March\u00a02012"

    def test_none_returns_none(self):
        assert _format_date(None, "nl") is None

    def test_unknown_language_falls_back_to_english(self):
        result = _format_date(self._dt(2020, 1, 1), "de")
        assert "January" in result

    def test_all_nl_months(self):
        expected = [
            "januari", "februari", "maart", "april", "mei", "juni",
            "juli", "augustus", "september", "oktober", "november", "december",
        ]
        for month_num, name in enumerate(expected, start=1):
            result = _format_date(self._dt(2020, month_num, 1), "nl")
            assert name in result

    def test_uses_narrow_no_break_space(self):
        result = _format_date(self._dt(2020, 5, 1), "nl")
        # \u00a0 is the non-breaking space used as separator
        assert "\u00a0" in result


class TestFormatEditCount:
    def test_none_returns_none(self):
        assert _format_edit_count(None) is None

    def test_below_1000(self):
        assert _format_edit_count(999) == "999"

    def test_thousands_separator(self):
        result = _format_edit_count(12_847)
        # narrow no-break space \u202f as thousands separator
        assert result == "12\u202f847"

    def test_millions(self):
        result = _format_edit_count(1_234_567)
        assert result == "1\u202f234\u202f567"

    def test_zero(self):
        assert _format_edit_count(0) == "0"

    def test_exact_thousand(self):
        assert _format_edit_count(1000) == "1\u202f000"


class TestImageUrl:
    """
    _image_url builds file:// URLs for WeasyPrint to load downloaded images.

    It must handle None inputs gracefully (preview mode has no image_dir)
    and return None when the file does not exist on disk yet.
    """

    def test_returns_none_when_filename_is_none(self, tmp_path):
        result = _image_url(filename=None, image_dir=str(tmp_path))

        assert result is None

    def test_returns_none_when_image_dir_is_none(self):
        result = _image_url(filename='Avatar.jpg', image_dir=None)

        assert result is None

    def test_returns_none_when_file_does_not_exist(self, tmp_path):
        """A filename that hasn't been downloaded yet must not produce a broken URL."""
        result = _image_url(filename='Missing.jpg', image_dir=str(tmp_path))

        assert result is None

    def test_returns_file_url_for_existing_file(self, tmp_path):
        """A downloaded image must produce a valid file:// URL for WeasyPrint."""
        images_dir = tmp_path / 'images'
        images_dir.mkdir()
        (images_dir / 'Avatar.jpg').write_bytes(b'fake-image-data')

        result = _image_url(filename='Avatar.jpg', image_dir=str(tmp_path))

        assert result is not None
        assert result.startswith('file://')
        assert 'Avatar.jpg' in result

    def test_url_uses_three_slashes_for_absolute_path(self, tmp_path):
        """
        WeasyPrint requires exactly three slashes for absolute paths:
        file:///absolute/path — not file://relative/path.
        """
        images_dir = tmp_path / 'images'
        images_dir.mkdir()
        (images_dir / 'Test.jpg').write_bytes(b'x')

        result = _image_url(filename='Test.jpg', image_dir=str(tmp_path))

        # The path after the protocol must start with /
        assert result.startswith('file:///')


class TestBuildCardContext:
    """
    build_card_context converts a UserProfile ORM row into the Jinja2 template
    context. It must handle missing JSON fields, None values, and malformed JSON
    without raising, falling back to empty lists or None as appropriate.
    """

    def _make_profile(self, **kwargs):
        """Return a SimpleNamespace mimicking a UserProfile with sensible defaults."""
        defaults = dict(
            username='TestUser',
            avatar_filename=None,
            home_base=None,
            biography=None,
            biography_image_filename=None,
            extra_sections=None,
            userboxes=None,
            barnstars=None,
            selected_achievements=None,
            proud_of=None,
            signature_html=None,
            registration_date=None,
            total_edits=None,
            user_rights=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_empty_profile_does_not_raise(self, flask_app):
        """A profile with all None fields must produce a valid context without errors."""
        from src.render import build_card_context
        import json

        profile = self._make_profile()

        with flask_app.app_context():
            ctx = build_card_context(profile=profile, image_dir=None)

        assert ctx['profile'].username == 'TestUser'
        assert ctx['profile'].userboxes == []
        assert ctx['profile'].barnstars == []
        assert ctx['profile'].proud_of  == []

    def test_malformed_json_field_falls_back_to_empty_list(self, flask_app):
        """Corrupted JSON stored in the DB must not crash the render pipeline."""
        from src.render import build_card_context

        profile = self._make_profile(userboxes='not-valid-json{{{')

        with flask_app.app_context():
            ctx = build_card_context(profile=profile, image_dir=None)

        assert ctx['profile'].userboxes == []

    def test_username_passed_through(self, flask_app):
        from src.render import build_card_context

        profile = self._make_profile(username='Multichill')

        with flask_app.app_context():
            ctx = build_card_context(profile=profile, image_dir=None)

        assert ctx['profile'].username == 'Multichill'

    def test_biography_passed_through(self, flask_app):
        from src.render import build_card_context

        profile = self._make_profile(biography='I edit Wikipedia since 2005.')

        with flask_app.app_context():
            ctx = build_card_context(profile=profile, image_dir=None)

        assert ctx['profile'].biography == 'I edit Wikipedia since 2005.'
