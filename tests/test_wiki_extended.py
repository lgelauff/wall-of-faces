"""
Tests for the remaining wiki.py functions not covered by test_wiki.py:
  get_userpage_wikitext, get_talkpage_wikitext,
  get_talk_subpage_titles, get_userpage_images,
  get_commons_category_images, get_wikidata_p18
"""
import pytest
from unittest.mock import patch, call


# ── get_userpage_wikitext / get_talkpage_wikitext ─────────────────────────────

class TestGetUserpageWikitext:
    """Thin wrappers — verify they call get_wikitext with the right title."""

    @patch("src.wiki.get_wikitext", return_value="userpage content")
    def test_calls_get_wikitext_with_user_prefix(self, mock_gw):
        from src.wiki import get_userpage_wikitext
        result = get_userpage_wikitext("https://nl.wikipedia.org", "Testuser")
        mock_gw.assert_called_once_with("https://nl.wikipedia.org", "User:Testuser")
        assert result == "userpage content"

    @patch("src.wiki.get_wikitext", return_value="")
    def test_returns_empty_string_when_page_missing(self, mock_gw):
        from src.wiki import get_userpage_wikitext
        assert get_userpage_wikitext("https://nl.wikipedia.org", "Ghost") == ""


class TestGetTalkpageWikitext:
    @patch("src.wiki.get_wikitext", return_value="talk content")
    def test_calls_get_wikitext_with_user_talk_prefix(self, mock_gw):
        from src.wiki import get_talkpage_wikitext
        result = get_talkpage_wikitext("https://nl.wikipedia.org", "Testuser")
        mock_gw.assert_called_once_with("https://nl.wikipedia.org", "User talk:Testuser")
        assert result == "talk content"


# ── get_talk_subpage_titles ───────────────────────────────────────────────────

class TestGetTalkSubpageTitles:
    def _make_response(self, titles):
        return {
            "query": {
                "allpages": [{"title": t} for t in titles]
            }
        }

    @patch("src.wiki._api")
    def test_returns_list_of_titles(self, mock_api):
        mock_api.return_value = self._make_response([
            "User talk:Foo/Archive 1",
            "User talk:Foo/Archive 2",
        ])
        from src.wiki import get_talk_subpage_titles
        result = get_talk_subpage_titles("https://nl.wikipedia.org", "Foo")
        assert result == ["User talk:Foo/Archive 1", "User talk:Foo/Archive 2"]

    @patch("src.wiki._api")
    def test_empty_allpages_returns_empty(self, mock_api):
        mock_api.return_value = {"query": {"allpages": []}}
        from src.wiki import get_talk_subpage_titles
        assert get_talk_subpage_titles("https://nl.wikipedia.org", "Foo") == []

    @patch("src.wiki._api")
    def test_api_error_returns_empty(self, mock_api):
        mock_api.side_effect = Exception("timeout")
        from src.wiki import get_talk_subpage_titles
        assert get_talk_subpage_titles("https://nl.wikipedia.org", "Foo") == []

    @patch("src.wiki._api")
    def test_uses_namespace_3(self, mock_api):
        mock_api.return_value = {"query": {"allpages": []}}
        from src.wiki import get_talk_subpage_titles
        get_talk_subpage_titles("https://nl.wikipedia.org", "Foo")
        _, kwargs_or_args = mock_api.call_args
        # _api is called positionally: _api(wiki_url, params)
        params = mock_api.call_args[0][1]
        assert params.get('apnamespace') == '3'

    @patch("src.wiki._api")
    def test_prefix_is_username_slash(self, mock_api):
        mock_api.return_value = {"query": {"allpages": []}}
        from src.wiki import get_talk_subpage_titles
        get_talk_subpage_titles("https://nl.wikipedia.org", "MyUser")
        params = mock_api.call_args[0][1]
        assert params.get('apprefix') == "MyUser/"


# ── get_userpage_images ───────────────────────────────────────────────────────

class TestGetUserpageImages:
    def _make_response(self, images, missing=False):
        page = {"images": [{"title": t} for t in images]}
        if missing:
            page = {"missing": True}
        return {"query": {"pages": [page]}}

    @patch("src.wiki._api")
    def test_strips_file_prefix(self, mock_api):
        mock_api.return_value = self._make_response(["File:Photo.jpg"])
        from src.wiki import get_userpage_images
        result = get_userpage_images("https://nl.wikipedia.org", "Foo")
        assert result == ["Photo.jpg"]

    @patch("src.wiki._api")
    def test_strips_bestand_prefix(self, mock_api):
        mock_api.return_value = self._make_response(["Bestand:Afbeelding.png"])
        from src.wiki import get_userpage_images
        result = get_userpage_images("https://nl.wikipedia.org", "Foo")
        assert result == ["Afbeelding.png"]

    @patch("src.wiki._api")
    def test_non_file_images_filtered_out(self, mock_api):
        mock_api.return_value = self._make_response([
            "File:Good.jpg",
            "Category:Something",   # not a File: — must be filtered
            "Template:Foo",
        ])
        from src.wiki import get_userpage_images
        result = get_userpage_images("https://nl.wikipedia.org", "Foo")
        assert result == ["Good.jpg"]

    @patch("src.wiki._api")
    def test_missing_page_returns_empty(self, mock_api):
        mock_api.return_value = self._make_response([], missing=True)
        from src.wiki import get_userpage_images
        assert get_userpage_images("https://nl.wikipedia.org", "Ghost") == []

    @patch("src.wiki._api")
    def test_empty_pages_returns_empty(self, mock_api):
        mock_api.return_value = {"query": {"pages": []}}
        from src.wiki import get_userpage_images
        assert get_userpage_images("https://nl.wikipedia.org", "Foo") == []

    @patch("src.wiki._api")
    def test_api_error_returns_empty(self, mock_api):
        mock_api.side_effect = Exception("error")
        from src.wiki import get_userpage_images
        assert get_userpage_images("https://nl.wikipedia.org", "Foo") == []

    @patch("src.wiki._api")
    def test_multiple_images_returned(self, mock_api):
        mock_api.return_value = self._make_response([
            "File:A.jpg", "File:B.jpg", "File:C.jpg"
        ])
        from src.wiki import get_userpage_images
        result = get_userpage_images("https://nl.wikipedia.org", "Foo")
        assert result == ["A.jpg", "B.jpg", "C.jpg"]

    @patch("src.wiki._api")
    def test_no_double_prefix_stripping(self, mock_api):
        # 'File:Bestand:Foo.jpg' — removeprefix is chained; only one prefix removed
        mock_api.return_value = self._make_response(["File:Bestand:Foo.jpg"])
        from src.wiki import get_userpage_images
        result = get_userpage_images("https://nl.wikipedia.org", "Foo")
        # After removeprefix('File:') → 'Bestand:Foo.jpg', then removeprefix('Bestand:') → 'Foo.jpg'
        assert result == ["Foo.jpg"]


# ── get_commons_category_images ───────────────────────────────────────────────

class TestGetCommonsCategoryImages:
    def _make_response(self, titles):
        return {
            "query": {
                "categorymembers": [{"title": t} for t in titles]
            }
        }

    @patch("src.wiki._api")
    def test_returns_filenames_from_first_match(self, mock_api):
        mock_api.return_value = self._make_response(["File:A.jpg", "File:B.jpg"])
        from src.wiki import get_commons_category_images
        result = get_commons_category_images("Foo")
        assert result == ["A.jpg", "B.jpg"]

    @patch("src.wiki._api")
    def test_stops_at_first_nonempty_category(self, mock_api):
        # First call returns results — second should not be called
        mock_api.return_value = self._make_response(["File:A.jpg"])
        from src.wiki import get_commons_category_images
        get_commons_category_images("Foo")
        assert mock_api.call_count == 1

    @patch("src.wiki._api")
    def test_tries_next_pattern_when_empty(self, mock_api):
        empty = {"query": {"categorymembers": []}}
        found = self._make_response(["File:Photo.jpg"])
        # First call empty, second call has results
        mock_api.side_effect = [empty, found]
        from src.wiki import get_commons_category_images
        result = get_commons_category_images("Foo")
        assert result == ["Photo.jpg"]
        assert mock_api.call_count == 2

    @patch("src.wiki._api")
    def test_all_patterns_empty_returns_empty(self, mock_api):
        mock_api.return_value = {"query": {"categorymembers": []}}
        from src.wiki import get_commons_category_images
        result = get_commons_category_images("Foo")
        assert result == []
        assert mock_api.call_count == 4   # all four patterns tried

    @patch("src.wiki._api")
    def test_exception_continues_to_next_pattern(self, mock_api):
        found = self._make_response(["File:X.jpg"])
        mock_api.side_effect = [Exception("error"), found]
        from src.wiki import get_commons_category_images
        result = get_commons_category_images("Foo")
        assert result == ["X.jpg"]

    @patch("src.wiki._api")
    def test_all_exceptions_returns_empty(self, mock_api):
        mock_api.side_effect = Exception("always fails")
        from src.wiki import get_commons_category_images
        assert get_commons_category_images("Foo") == []

    @patch("src.wiki._api")
    def test_non_file_members_filtered(self, mock_api):
        mock_api.return_value = self._make_response([
            "File:Good.jpg", "Category:Something"
        ])
        from src.wiki import get_commons_category_images
        result = get_commons_category_images("Foo")
        assert result == ["Good.jpg"]

    @patch("src.wiki._api")
    def test_first_category_pattern_uses_photographs_by(self, mock_api):
        mock_api.return_value = {"query": {"categorymembers": []}}
        from src.wiki import get_commons_category_images
        get_commons_category_images("TestUser")
        first_params = mock_api.call_args_list[0][0][1]
        assert first_params['cmtitle'] == "Category:Photographs by TestUser"


# ── get_wikidata_p18 ──────────────────────────────────────────────────────────

class TestGetWikidataP18:
    def _userpage_response(self, qid):
        page = {"pageprops": {"wikibase_item": qid}} if qid else {"pageprops": {}}
        return {"query": {"pages": [page]}}

    def _p18_response(self, filename):
        if filename is None:
            return {"claims": {}}
        return {
            "claims": {
                "P18": [{
                    "mainsnak": {
                        "datavalue": {"value": filename}
                    }
                }]
            }
        }

    @patch("src.wiki._api")
    def test_returns_p18_filename(self, mock_api):
        mock_api.side_effect = [
            self._userpage_response("Q12345"),
            self._p18_response("Portrait.jpg"),
        ]
        from src.wiki import get_wikidata_p18
        result = get_wikidata_p18("https://nl.wikipedia.org", "Foo")
        assert result == "Portrait.jpg"

    @patch("src.wiki._api")
    def test_no_wikibase_item_returns_none(self, mock_api):
        mock_api.return_value = self._userpage_response(None)
        from src.wiki import get_wikidata_p18
        assert get_wikidata_p18("https://nl.wikipedia.org", "Foo") is None

    @patch("src.wiki._api")
    def test_empty_pages_returns_none(self, mock_api):
        mock_api.return_value = {"query": {"pages": []}}
        from src.wiki import get_wikidata_p18
        assert get_wikidata_p18("https://nl.wikipedia.org", "Foo") is None

    @patch("src.wiki._api")
    def test_no_p18_claim_returns_none(self, mock_api):
        mock_api.side_effect = [
            self._userpage_response("Q12345"),
            self._p18_response(None),
        ]
        from src.wiki import get_wikidata_p18
        assert get_wikidata_p18("https://nl.wikipedia.org", "Foo") is None

    @patch("src.wiki._api")
    def test_p18_value_not_string_returns_none(self, mock_api):
        # Wikidata can return dict values for some property types
        mock_api.side_effect = [
            self._userpage_response("Q12345"),
            {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": {"type": "dict"}}}}]}},
        ]
        from src.wiki import get_wikidata_p18
        assert get_wikidata_p18("https://nl.wikipedia.org", "Foo") is None

    @patch("src.wiki._api")
    def test_api_error_returns_none(self, mock_api):
        mock_api.side_effect = Exception("network error")
        from src.wiki import get_wikidata_p18
        assert get_wikidata_p18("https://nl.wikipedia.org", "Foo") is None

    @patch("src.wiki._api")
    def test_second_api_call_error_returns_none(self, mock_api):
        mock_api.side_effect = [
            self._userpage_response("Q12345"),
            Exception("wikidata down"),
        ]
        from src.wiki import get_wikidata_p18
        assert get_wikidata_p18("https://nl.wikipedia.org", "Foo") is None
