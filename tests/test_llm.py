"""Tests for src/llm.py — output validation, sanitisation, truncation."""
import pytest
from unittest.mock import patch

from src.llm import (
    extract_userbox_suggestions,
    extract_proud_of_suggestions,
    MISTRAL_MAX_WIKITEXT_CHARS,
    _MAX_ICON_LEN,
    _MAX_LABEL_LEN,
    _MAX_TITLE_LEN,
    _VALID_TYPES,
)


def _mock_call(return_value):
    """Patch _call_mistral to return return_value."""
    return patch("src.llm._call_mistral", return_value=return_value)


class TestExtractUserboxSuggestions:
    def test_empty_wikitext_returns_empty(self):
        assert extract_userbox_suggestions("") == []

    def test_valid_output_passed_through(self):
        items = [{"icon": "NL", "label": "Moedertaal Nederlands"}]
        with _mock_call(items):
            result = extract_userbox_suggestions("some wikitext")
        assert result == items

    def test_strips_whitespace(self):
        items = [{"icon": "  NL  ", "label": "  test  "}]
        with _mock_call(items):
            result = extract_userbox_suggestions("x")
        assert result[0]["icon"] == "NL"
        assert result[0]["label"] == "test"

    def test_icon_truncated_at_max_len(self):
        long_icon = "A" * (_MAX_ICON_LEN + 10)
        items = [{"icon": long_icon, "label": "test"}]
        with _mock_call(items):
            result = extract_userbox_suggestions("x")
        assert len(result[0]["icon"]) == _MAX_ICON_LEN

    def test_label_truncated_at_max_len(self):
        long_label = "B" * (_MAX_LABEL_LEN + 10)
        items = [{"icon": "x", "label": long_label}]
        with _mock_call(items):
            result = extract_userbox_suggestions("x")
        assert len(result[0]["label"]) == _MAX_LABEL_LEN

    def test_empty_label_filtered_out(self):
        items = [{"icon": "NL", "label": ""}]
        with _mock_call(items):
            result = extract_userbox_suggestions("x")
        assert result == []

    def test_non_string_icon_filtered(self):
        items = [{"icon": 42, "label": "valid label"}]
        with _mock_call(items):
            result = extract_userbox_suggestions("x")
        assert result == []

    def test_non_dict_items_filtered(self):
        with _mock_call(["not", "a", "dict"]):
            result = extract_userbox_suggestions("x")
        assert result == []

    def test_llm_error_returns_empty(self):
        # _call_mistral catches all exceptions and returns []; verify the chain
        with patch("src.llm._call_mistral", return_value=[]):
            result = extract_userbox_suggestions("x")
        assert result == []

    def test_all_valid_items_passed_through(self):
        # The _MAX_ITEMS cap is enforced inside _call_mistral (before it returns).
        # extract_userbox_suggestions passes all items from _call_mistral through
        # subject only to field validation. Verify no extra filtering is applied.
        from src.llm import _MAX_ITEMS
        many = [{"icon": "x", "label": f"label {i}"} for i in range(_MAX_ITEMS)]
        with _mock_call(many):
            result = extract_userbox_suggestions("x")
        assert len(result) == _MAX_ITEMS

    def test_wikitext_truncation(self):
        """Oversized wikitext is truncated to MISTRAL_MAX_WIKITEXT_CHARS before sending."""
        from src.llm import _USERBOX_PROMPT
        captured = []

        def fake_call(prompt):
            captured.append(prompt)
            return []

        big_wikitext = "x" * (MISTRAL_MAX_WIKITEXT_CHARS * 2)
        with patch("src.llm._call_mistral", side_effect=fake_call):
            extract_userbox_suggestions(big_wikitext)

        assert captured, "Expected _call_mistral to be called"
        # The wikitext in the prompt must be exactly MISTRAL_MAX_WIKITEXT_CHARS 'x' chars
        assert "x" * MISTRAL_MAX_WIKITEXT_CHARS in captured[0]
        assert "x" * (MISTRAL_MAX_WIKITEXT_CHARS + 1) not in captured[0]


class TestExtractProudOfSuggestions:
    def test_empty_wikitext_returns_empty(self):
        assert extract_proud_of_suggestions("") == []

    def test_valid_output(self):
        items = [{"title": "Watersnoodramp 1953", "type": "article"}]
        with _mock_call(items):
            result = extract_proud_of_suggestions("x")
        assert result == items

    def test_title_truncated(self):
        long_title = "T" * (_MAX_TITLE_LEN + 10)
        with _mock_call([{"title": long_title, "type": "article"}]):
            result = extract_proud_of_suggestions("x")
        assert len(result[0]["title"]) == _MAX_TITLE_LEN

    def test_invalid_type_normalised_to_other(self):
        with _mock_call([{"title": "Foo", "type": "video"}]):
            result = extract_proud_of_suggestions("x")
        assert result[0]["type"] == "other"

    def test_valid_types_accepted(self):
        for t in _VALID_TYPES:
            with _mock_call([{"title": "Foo", "type": t}]):
                result = extract_proud_of_suggestions("x")
            assert result[0]["type"] == t

    def test_empty_title_filtered(self):
        with _mock_call([{"title": "", "type": "article"}]):
            result = extract_proud_of_suggestions("x")
        assert result == []

    def test_non_string_type_filtered(self):
        # Non-string type is a malformed object — discarded per design
        with _mock_call([{"title": "Foo", "type": 123}]):
            result = extract_proud_of_suggestions("x")
        assert result == []

    def test_non_dict_items_filtered(self):
        with _mock_call(["string", None, 42]):
            result = extract_proud_of_suggestions("x")
        assert result == []

    def test_type_case_insensitive(self):
        with _mock_call([{"title": "Foo", "type": "ARTICLE"}]):
            result = extract_proud_of_suggestions("x")
        assert result[0]["type"] == "article"
