"""Tests for src/i18n.py"""
import os
import pytest
import tempfile

from src.i18n import load_strings, make_t, get_card_strings


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_strings_dir(tmp_path):
    """Create a minimal strings/ directory with nl.yml and en.yml."""
    strings_dir = tmp_path / "strings"
    strings_dir.mkdir()
    (strings_dir / "nl.yml").write_text(
        "nav.logout: Uitloggen\n"
        "nav.back: Terug\n"
        "card.member_since: Actief sinds\n"
        "card.user_prefix: Gebruiker\n"
        "card.home_base: Thuisbasis\n"
        "card.edit_count: Bewerkingen\n"
        "card.rights: Rechten\n"
        "card.proud_of: Trots op\n"
        "card.achievements: Prestaties\n"
        "card.biography_image_alt: Bio afbeelding\n"
        "card.no_image: geen afbeelding\n"
        "gather.error: 'Fout: {message}'\n",
        encoding="utf-8",
    )
    (strings_dir / "en.yml").write_text(
        "nav.logout: Log out\n"
        "card.member_since: Active since\n"
        "card.user_prefix: User\n"
        "card.home_base: Home base\n"
        "card.edit_count: Edits\n"
        "card.rights: Rights\n"
        "card.proud_of: Proud of\n"
        "card.achievements: Achievements\n"
        "card.biography_image_alt: Biography image\n"
        "card.no_image: no image\n",
        encoding="utf-8",
    )
    return str(tmp_path)


# ── load_strings ──────────────────────────────────────────────────────────────

class TestLoadStrings:
    def test_loads_nl_strings(self, tmp_strings_dir):
        strings = load_strings("nl", tmp_strings_dir)
        assert strings["nav.logout"] == "Uitloggen"

    def test_loads_en_strings(self, tmp_strings_dir):
        strings = load_strings("en", tmp_strings_dir)
        assert strings["nav.logout"] == "Log out"

    def test_missing_language_falls_back_to_en(self, tmp_strings_dir):
        strings = load_strings("de", tmp_strings_dir)
        # 'de' doesn't exist → falls back to en.yml
        assert strings["nav.logout"] == "Log out"

    def test_returns_dict(self, tmp_strings_dir):
        result = load_strings("nl", tmp_strings_dir)
        assert isinstance(result, dict)

    def test_empty_file_returns_empty_dict(self, tmp_path):
        strings_dir = tmp_path / "strings"
        strings_dir.mkdir()
        (strings_dir / "en.yml").write_text("", encoding="utf-8")
        result = load_strings("nl", str(tmp_path))  # nl missing → en fallback
        assert result == {}

    def test_all_keys_are_strings(self, tmp_strings_dir):
        strings = load_strings("nl", tmp_strings_dir)
        for k, v in strings.items():
            assert isinstance(k, str), f"Key {k!r} is not a string"
            assert isinstance(v, str), f"Value for {k!r} is not a string"


# ── make_t ────────────────────────────────────────────────────────────────────

class TestMakeT:
    def setup_method(self):
        self.strings = {
            "nav.logout": "Uitloggen",
            "gather.error": "Fout: {message}",
            "key.with.braces": "Waarde: {n}",
        }
        self.t = make_t(self.strings)

    def test_known_key_returned(self):
        assert self.t("nav.logout") == "Uitloggen"

    def test_missing_key_returns_key_itself(self):
        assert self.t("nonexistent.key") == "nonexistent.key"

    def test_format_kwargs_substituted(self):
        result = self.t("gather.error", message="verbinding verbroken")
        assert result == "Fout: verbinding verbroken"

    def test_missing_format_kwarg_returns_unformatted(self):
        # KeyError in format → returns the template string as-is
        result = self.t("gather.error")   # no message kwarg
        assert "{message}" in result

    def test_extra_kwargs_ignored(self):
        # Extra kwargs that don't appear in the template are ignored
        result = self.t("nav.logout", unused="x")
        assert result == "Uitloggen"

    def test_multiple_kwargs(self):
        strings = {"msg": "Hello {name}, you have {n} messages"}
        t = make_t(strings)
        assert t("msg", name="Alice", n=3) == "Hello Alice, you have 3 messages"

    def test_empty_strings_dict(self):
        t = make_t({})
        assert t("any.key") == "any.key"


# ── get_card_strings ──────────────────────────────────────────────────────────

class TestGetCardStrings:
    def test_returns_simple_namespace(self, tmp_strings_dir):
        from types import SimpleNamespace
        strings = load_strings("nl", tmp_strings_dir)
        ns = get_card_strings(strings)
        assert isinstance(ns, SimpleNamespace)

    def test_member_since_populated(self, tmp_strings_dir):
        strings = load_strings("nl", tmp_strings_dir)
        ns = get_card_strings(strings)
        assert ns.member_since == "Actief sinds"

    def test_missing_card_key_falls_back_to_key_name(self):
        # Strings dict has no card.* keys
        ns = get_card_strings({})
        assert ns.member_since == "member_since"
        assert ns.user_prefix == "user_prefix"

    def test_all_required_attributes_present(self, tmp_strings_dir):
        strings = load_strings("nl", tmp_strings_dir)
        ns = get_card_strings(strings)
        for attr in ("user_prefix", "member_since", "home_base", "edit_count",
                     "rights", "proud_of", "achievements",
                     "biography_image_alt", "no_image"):
            assert hasattr(ns, attr), f"Missing attribute: {attr!r}"

    def test_en_strings_loaded_correctly(self, tmp_strings_dir):
        strings = load_strings("en", tmp_strings_dir)
        ns = get_card_strings(strings)
        assert ns.member_since == "Active since"


# ── Real nl.yml file smoke test ───────────────────────────────────────────────

class TestRealNlYml:
    """Verify the actual nl.yml ships all keys used by the application."""

    REQUIRED_KEYS = [
        "nav.logout", "nav.back", "nav.save", "nav.finalize",
        "index.title", "index.login",
        "gather.title", "gather.progress", "gather.done", "gather.error",
        "buffet.title", "buffet.accept", "buffet.reject",
        "card.member_since", "card.user_prefix", "card.edit_count",
        "finalize.overflow_error",
        "error.not_found", "error.forbidden", "error.server",
    ]

    def test_all_required_keys_present(self):
        repo_root = os.path.join(os.path.dirname(__file__), "..")
        strings = load_strings("nl", repo_root)
        missing = [k for k in self.REQUIRED_KEYS if k not in strings]
        assert not missing, f"Missing keys in nl.yml: {missing}"
