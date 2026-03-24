"""Tests for src/achievements.py"""
from datetime import datetime, timedelta, timezone
import pytest
from src.achievements import eligible_achievements, get_selected_badges, REGISTRY


def _reg(years_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=years_ago * 365.25)


class TestEligibleAchievements:
    def test_no_data_returns_empty(self):
        result = eligible_achievements(None, None, 0, None, None)
        assert result == []

    # ── Tenure badges ────────────────────────────────────────────────────────

    def test_veteran_at_5_years(self):
        ids = {b.id for b in eligible_achievements(_reg(5.1), 0, 0, None, None)}
        assert "veteran" in ids
        assert "decade" not in ids

    def test_decade_at_10_years(self):
        ids = {b.id for b in eligible_achievements(_reg(10.1), 0, 0, None, None)}
        assert "decade" in ids
        assert "veteran" not in ids  # decade replaces veteran

    def test_no_tenure_under_5_years(self):
        ids = {b.id for b in eligible_achievements(_reg(4.9), 0, 0, None, None)}
        assert "veteran" not in ids
        assert "decade" not in ids

    def test_at_threshold_days_5_years_gets_veteran(self):
        # Threshold is 5*365+2 = 1827 days
        reg = datetime.now(timezone.utc) - timedelta(days=1827)
        ids = {b.id for b in eligible_achievements(reg, 0, 0, None, None)}
        assert "veteran" in ids

    def test_one_day_before_veteran_threshold_no_badge(self):
        reg = datetime.now(timezone.utc) - timedelta(days=1826)
        ids = {b.id for b in eligible_achievements(reg, 0, 0, None, None)}
        assert "veteran" not in ids

    def test_at_threshold_days_10_years_gets_decade_not_veteran(self):
        # Threshold is 10*365+3 = 3653 days
        reg = datetime.now(timezone.utc) - timedelta(days=3653)
        ids = {b.id for b in eligible_achievements(reg, 0, 0, None, None)}
        assert "decade" in ids
        assert "veteran" not in ids

    def test_one_day_before_decade_threshold_gets_veteran(self):
        reg = datetime.now(timezone.utc) - timedelta(days=3652)
        ids = {b.id for b in eligible_achievements(reg, 0, 0, None, None)}
        assert "veteran" in ids
        assert "decade" not in ids

    def test_none_registration_no_tenure_badge(self):
        ids = {b.id for b in eligible_achievements(None, 50_000, 0, None, None)}
        assert "veteran" not in ids

    # ── Edit count badges ────────────────────────────────────────────────────

    def test_10k_badge(self):
        ids = {b.id for b in eligible_achievements(None, 10_000, 0, None, None)}
        assert "prolific_10k" in ids

    def test_50k_badge_not_10k(self):
        ids = {b.id for b in eligible_achievements(None, 50_000, 0, None, None)}
        assert "prolific_50k" in ids
        assert "prolific_10k" not in ids

    def test_49999_edits_gets_10k_not_50k(self):
        ids = {b.id for b in eligible_achievements(None, 49_999, 0, None, None)}
        assert "prolific_10k" in ids
        assert "prolific_50k" not in ids

    def test_100k_badge_not_lower_tiers(self):
        ids = {b.id for b in eligible_achievements(None, 100_000, 0, None, None)}
        assert "prolific_100k" in ids
        assert "prolific_50k" not in ids
        assert "prolific_10k" not in ids

    def test_99999_edits_gets_50k_not_100k(self):
        ids = {b.id for b in eligible_achievements(None, 99_999, 0, None, None)}
        assert "prolific_50k" in ids
        assert "prolific_100k" not in ids

    def test_below_10k_no_edit_badge(self):
        ids = {b.id for b in eligible_achievements(None, 9_999, 0, None, None)}
        assert not any(i.startswith("prolific") for i in ids)

    def test_none_edits_no_badge(self):
        ids = {b.id for b in eligible_achievements(None, None, 0, None, None)}
        assert not any(i.startswith("prolific") for i in ids)

    # ── Cross-wiki ───────────────────────────────────────────────────────────

    def test_multilingual_at_3_wikis(self):
        ids = {b.id for b in eligible_achievements(None, 0, 3, None, None)}
        assert "multilingual" in ids

    def test_no_multilingual_below_3(self):
        ids = {b.id for b in eligible_achievements(None, 0, 2, None, None)}
        assert "multilingual" not in ids

    # ── Commons ──────────────────────────────────────────────────────────────

    def test_commons_contributor(self):
        ids = {b.id for b in eligible_achievements(None, 0, 0, 1, None)}
        assert "commons_contributor" in ids

    def test_visual_historian_at_100_commons_edits(self):
        ids = {b.id for b in eligible_achievements(None, 0, 0, 100, None)}
        assert "visual_historian" in ids
        assert "commons_contributor" in ids

    def test_no_visual_historian_below_100(self):
        ids = {b.id for b in eligible_achievements(None, 0, 0, 99, None)}
        assert "visual_historian" not in ids
        assert "commons_contributor" in ids

    def test_zero_commons_no_badge(self):
        ids = {b.id for b in eligible_achievements(None, 0, 0, 0, None)}
        assert "commons_contributor" not in ids

    # ── Wikidata ─────────────────────────────────────────────────────────────

    def test_wikidata_contributor(self):
        ids = {b.id for b in eligible_achievements(None, 0, 0, None, 1)}
        assert "wikidata_contributor" in ids

    def test_zero_wikidata_no_badge(self):
        ids = {b.id for b in eligible_achievements(None, 0, 0, None, 0)}
        assert "wikidata_contributor" not in ids

    # ── Combined ─────────────────────────────────────────────────────────────

    def test_all_badges_combo(self):
        ids = {b.id for b in eligible_achievements(
            _reg(11), 110_000, 4, 200, 50
        )}
        assert {"decade", "prolific_100k", "multilingual",
                "commons_contributor", "visual_historian",
                "wikidata_contributor"}.issubset(ids)
        assert "veteran" not in ids


class TestGetSelectedBadges:
    def test_known_ids(self):
        badges = get_selected_badges(["veteran", "decade"])
        assert [b.id for b in badges] == ["veteran", "decade"]

    def test_unknown_id_silently_skipped(self):
        badges = get_selected_badges(["veteran", "nonexistent"])
        assert len(badges) == 1
        assert badges[0].id == "veteran"

    def test_empty_list(self):
        assert get_selected_badges([]) == []

    def test_all_registry_ids_valid(self):
        ids = list(REGISTRY.keys())
        badges = get_selected_badges(ids)
        assert len(badges) == len(ids)
