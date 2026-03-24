"""Tests for src/snapshot.py — pure functions (no Flask context needed)."""
import os
import pytest
from src.snapshot import safe_username_dir, VERSION_RE


class TestSafeUsernameDir:
    def test_normal_username(self):
        result = safe_username_dir("Multichill", 42)
        assert result == "Multichill_42"

    def test_spaces_replaced_with_underscore(self):
        result = safe_username_dir("User Name", 1)
        assert " " not in result
        assert result.endswith("_1")

    def test_dots_replaced(self):
        result = safe_username_dir("User.Name", 5)
        assert "." not in result

    def test_underscore_prefix_fallback(self):
        # Username that becomes '_foo' → must use fallback
        result = safe_username_dir("!username", 7)
        # re.sub replaces '!' with '_', making it start with '_'
        assert result == "user_7"

    def test_empty_username_fallback(self):
        result = safe_username_dir("", 99)
        assert result == "user_99"

    def test_user_id_appended(self):
        r1 = safe_username_dir("User A", 1)
        r2 = safe_username_dir("User_A", 2)
        # Both normalise to 'User_A' prefix but differ by user_id
        assert r1 != r2
        assert r1.endswith("_1")
        assert r2.endswith("_2")

    def test_unicode_username(self):
        # Non-ASCII chars: \w matches unicode letters in Python re
        result = safe_username_dir("Ärger", 3)
        assert result.endswith("_3")
        assert " " not in result

    def test_hyphen_preserved(self):
        result = safe_username_dir("User-Name", 10)
        assert "User-Name_10" == result

    def test_path_traversal_sanitised(self):
        # '/' becomes '_', so '../../etc' → '______etc'
        result = safe_username_dir("../../etc", 1)
        assert ".." not in result
        assert "/" not in result
        assert result.endswith("_1")

    def test_absolute_path_sanitised(self):
        result = safe_username_dir("/etc/passwd", 2)
        assert "/" not in result
        assert result.endswith("_2")


class TestVersionRe:
    def test_valid_version(self):
        assert VERSION_RE.match("20260501_143022_412381")

    def test_short_microseconds_still_matches(self):
        assert VERSION_RE.match("20260501_143022_1")

    def test_latest_symlink_not_matched(self):
        assert not VERSION_RE.match("latest")

    def test_partial_match_not_accepted(self):
        # fullmatch semantics via ^ and $
        assert not VERSION_RE.match("bad_20260501_143022_412381")

    def test_missing_microseconds_not_matched(self):
        assert not VERSION_RE.match("20260501_143022")
