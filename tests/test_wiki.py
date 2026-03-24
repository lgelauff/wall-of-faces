"""Tests for src/wiki.py — dbname_to_url and _api response parsing (no live calls)."""
import pytest
from unittest.mock import MagicMock, patch

from src.wiki import dbname_to_url


class TestDbnameToUrl:
    # ── Standard Wikipedia ───────────────────────────────────────────────────
    def test_nlwiki(self):
        assert dbname_to_url("nlwiki") == "https://nl.wikipedia.org"

    def test_enwiki(self):
        assert dbname_to_url("enwiki") == "https://en.wikipedia.org"

    def test_dewiki(self):
        assert dbname_to_url("dewiki") == "https://de.wikipedia.org"

    def test_single_char_lang(self):
        # 'frwiki' → 'https://fr.wikipedia.org'
        assert dbname_to_url("frwiki") == "https://fr.wikipedia.org"

    # ── Special wikis ────────────────────────────────────────────────────────
    def test_commonswiki(self):
        assert dbname_to_url("commonswiki") == "https://commons.wikimedia.org"

    def test_wikidatawiki(self):
        assert dbname_to_url("wikidatawiki") == "https://www.wikidata.org"

    def test_metawiki(self):
        assert dbname_to_url("metawiki") == "https://meta.wikimedia.org"

    def test_mediawikiwiki(self):
        assert dbname_to_url("mediawikiwiki") == "https://www.mediawiki.org"

    # ── Sister projects ──────────────────────────────────────────────────────
    def test_frwikisource(self):
        assert dbname_to_url("frwikisource") == "https://fr.wikisource.org"

    def test_enwiktionary(self):
        assert dbname_to_url("enwiktionary") == "https://en.wiktionary.org"

    def test_nlwikivoyage(self):
        assert dbname_to_url("nlwikivoyage") == "https://nl.wikivoyage.org"

    def test_enwikibooks(self):
        assert dbname_to_url("enwikibooks") == "https://en.wikibooks.org"

    # ── Unknown patterns → None ──────────────────────────────────────────────
    def test_unknown_returns_none(self):
        assert dbname_to_url("unknownwiki123") is None

    def test_empty_string(self):
        assert dbname_to_url("") is None

    def test_numeric_lang_code(self):
        # language codes must be alpha-only
        assert dbname_to_url("123wiki") is None


class TestGetUserRights:
    def _make_response(self, groups):
        return {
            "query": {"users": [{"name": "TestUser", "groups": groups}]}
        }

    @patch("src.wiki._api")
    def test_filters_universal_groups(self, mock_api):
        mock_api.return_value = self._make_response(
            ["*", "user", "autoconfirmed", "emailconfirmed", "sysop"]
        )
        from src.wiki import get_user_rights
        result = get_user_rights("https://nl.wikipedia.org", "TestUser")
        assert result == ["sysop"]

    @patch("src.wiki._api")
    def test_empty_groups(self, mock_api):
        mock_api.return_value = self._make_response([])
        from src.wiki import get_user_rights
        result = get_user_rights("https://nl.wikipedia.org", "TestUser")
        assert result == []

    @patch("src.wiki._api")
    def test_api_error_returns_empty(self, mock_api):
        mock_api.side_effect = Exception("network error")
        from src.wiki import get_user_rights
        result = get_user_rights("https://nl.wikipedia.org", "TestUser")
        assert result == []


class TestGetWikitext:
    @patch("src.wiki._api")
    def test_returns_content(self, mock_api):
        mock_api.return_value = {
            "query": {"pages": [{
                "revisions": [{"slots": {"main": {"content": "Hello wiki"}}}]
            }]}
        }
        from src.wiki import get_wikitext
        result = get_wikitext("https://nl.wikipedia.org", "User:Test")
        assert result == "Hello wiki"

    @patch("src.wiki._api")
    def test_missing_page_returns_empty(self, mock_api):
        mock_api.return_value = {
            "query": {"pages": [{"missing": True}]}
        }
        from src.wiki import get_wikitext
        result = get_wikitext("https://nl.wikipedia.org", "User:Nonexistent")
        assert result == ""

    @patch("src.wiki._api")
    def test_empty_pages_returns_empty(self, mock_api):
        mock_api.return_value = {"query": {"pages": []}}
        from src.wiki import get_wikitext
        result = get_wikitext("https://nl.wikipedia.org", "User:Test")
        assert result == ""

    @patch("src.wiki._api")
    def test_api_error_returns_empty(self, mock_api):
        mock_api.side_effect = Exception("timeout")
        from src.wiki import get_wikitext
        result = get_wikitext("https://nl.wikipedia.org", "User:Test")
        assert result == ""

    @patch("src.wiki._api")
    def test_content_truncated_at_char_limit(self, mock_api):
        from src.wiki import _MAX_WIKITEXT_CHARS
        big = "x" * (_MAX_WIKITEXT_CHARS + 1000)
        mock_api.return_value = {
            "query": {"pages": [{
                "revisions": [{"slots": {"main": {"content": big}}}]
            }]}
        }
        from src.wiki import get_wikitext
        result = get_wikitext("https://nl.wikipedia.org", "User:Test")
        assert len(result) == _MAX_WIKITEXT_CHARS

    @patch("src.wiki._api")
    def test_page_with_empty_revisions_returns_empty(self, mock_api):
        mock_api.return_value = {
            "query": {"pages": [{"revisions": []}]}
        }
        from src.wiki import get_wikitext
        # Empty revisions list → [][0] raises IndexError, caught by outer except → ""
        result = get_wikitext("https://nl.wikipedia.org", "User:Test")
        assert result == ""

    def test_wikiwiki_returns_none(self):
        # Guard: 'wikiwiki' must not be treated as lang='wiki' + 'wiki' suffix
        assert dbname_to_url("wikiwiki") is None
