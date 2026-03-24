"""Tests for src/gather.py — sanitise_signature (no live DB/API)."""
import pytest
from src.gather import sanitise_signature


class TestSanitiseSignature:
    def test_passthrough_safe_html(self):
        html = '<a href="/wiki/User:Foo" title="Foo">Foo</a>'
        result = sanitise_signature(html)
        assert "Foo" in result
        assert 'href="/wiki/User:Foo"' in result

    def test_script_tag_removed(self):
        result = sanitise_signature("<script>alert(1)</script>text")
        assert "<script>" not in result
        assert "text" in result

    def test_javascript_href_stripped(self):
        result = sanitise_signature('<a href="javascript:void(0)">x</a>')
        assert "javascript:" not in result

    def test_style_attribute_on_span_kept(self):
        result = sanitise_signature('<span style="color:red">text</span>')
        assert "color:red" in result

    def test_disallowed_attribute_removed(self):
        # 'class' is not in the allowed attributes for any tag
        result = sanitise_signature('<span class="foo">text</span>')
        assert 'class="foo"' not in result
        assert "text" in result

    def test_nested_allowed_tags(self):
        result = sanitise_signature("<b><i>text</i></b>")
        assert "<b>" in result
        assert "<i>" in result

    def test_empty_string_returns_empty(self):
        assert sanitise_signature("") == ""

    def test_none_input_returns_empty(self):
        assert sanitise_signature(None) == ""

    def test_plain_text_unchanged(self):
        result = sanitise_signature("Hello world")
        assert result == "Hello world"

    def test_html_comment_stripped(self):
        result = sanitise_signature("<!-- secret -->text")
        assert "secret" not in result
        assert "text" in result

    def test_img_tag_removed(self):
        result = sanitise_signature('<img src="http://evil.com/track.gif">')
        assert "<img" not in result

    def test_abbr_with_title_allowed(self):
        result = sanitise_signature('<abbr title="Wikipedia">WP</abbr>')
        assert "<abbr" in result
        assert 'title="Wikipedia"' in result

    def test_sup_and_sub_allowed(self):
        result = sanitise_signature("<sup>1</sup><sub>2</sub>")
        assert "<sup>" in result
        assert "<sub>" in result

    def test_data_uri_href_stripped(self):
        result = sanitise_signature('<a href="data:text/html,<script>alert(1)</script>">x</a>')
        assert "data:" not in result

    def test_on_event_handlers_removed(self):
        result = sanitise_signature('<span onmouseover="evil()">text</span>')
        assert "onmouseover" not in result
        assert "text" in result

    def test_onclick_removed(self):
        result = sanitise_signature('<a href="/wiki/Foo" onclick="steal()">Foo</a>')
        assert "onclick" not in result
