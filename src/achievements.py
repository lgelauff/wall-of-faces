"""
achievements.py — Achievement badge registry and assignment logic.

Badges are awarded based on gathered data (edit counts, wiki tenure, roles).
They are presented in the buffet as suggestions; the user accepts or rejects each one.
Accepted IDs are stored as a JSON list in UserProfile.selected_achievements.

Badge icons must be short text labels (≤ 4 chars), NOT emoji.
Emoji cannot be reliably embedded in PDF by WeasyPrint/Cairo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AchievementBadge:
    id: str
    name: str           # Display name (shown below the badge)
    icon: str           # Short label inside the circle, ≤ 4 chars, no emoji
    color_class: str    # One of: green / blue / purple / amber / red
    description: str    # Tooltip / buffet description


# ── Registry ──────────────────────────────────────────────────────────────────
# Each entry defines one achievable badge.  The `description` explains to the
# user why they are being offered this badge.

REGISTRY: dict[str, AchievementBadge] = {
    "veteran": AchievementBadge(
        id="veteran",
        name="Veteraan",
        icon="vet.",
        color_class="blue",
        description="Meer dan 5 jaar actief als redacteur.",
    ),
    "decade": AchievementBadge(
        id="decade",
        name="Tien jaar!",
        icon="10j",
        color_class="purple",
        description="Meer dan 10 jaar actief als redacteur.",
    ),
    "prolific_10k": AchievementBadge(
        id="prolific_10k",
        name="10 000+ bewerkingen",
        icon="10k",
        color_class="amber",
        description="Meer dan 10 000 bewerkingen in totaal.",
    ),
    "prolific_50k": AchievementBadge(
        id="prolific_50k",
        name="50 000+ bewerkingen",
        icon="50k",
        color_class="amber",
        description="Meer dan 50 000 bewerkingen in totaal.",
    ),
    "prolific_100k": AchievementBadge(
        id="prolific_100k",
        name="100 000+ bewerkingen",
        icon="100k",
        color_class="amber",
        description="Meer dan 100 000 bewerkingen in totaal.",
    ),
    "multilingual": AchievementBadge(
        id="multilingual",
        name="Meertalige bijdrager",
        icon="NxN",
        color_class="green",
        description="Actief op drie of meer Wikipedia-taalgemeenschappen.",
    ),
    "commons_contributor": AchievementBadge(
        id="commons_contributor",
        name="Commons-bijdrager",
        icon="cmns",
        color_class="green",
        description="Actief op Wikimedia Commons.",
    ),
    "wikidata_contributor": AchievementBadge(
        id="wikidata_contributor",
        name="Wikidata-architect",
        icon="wd",
        color_class="purple",
        description="Actief op Wikidata.",
    ),
    "visual_historian": AchievementBadge(
        id="visual_historian",
        name="Visueel historicus",
        icon="foto",
        color_class="green",
        description="Meer dan 100 afbeeldingen geüpload op Wikimedia Commons.",
    ),
    "patrol": AchievementBadge(
        id="patrol",
        name="Patrol expert",
        icon="ptrl",
        color_class="red",
        description="Actief in het bewaken van recente wijzigingen.",
    ),
    "mentor": AchievementBadge(
        id="mentor",
        name="Mentor",
        icon="ment",
        color_class="blue",
        description="Deelnemer aan het mentorprogramma.",
    ),
}


def eligible_achievements(
    registration_date: datetime | None,
    total_edits: int | None,
    wiki_count: int,
    commons_edits: int | None,
    wikidata_edits: int | None,
) -> list[AchievementBadge]:
    """
    Return the list of badges the user is eligible for, based on gathered stats.
    These are offered as buffet suggestions; user still accepts or rejects each one.
    """
    from datetime import timezone

    badges = []
    now = datetime.now(timezone.utc)

    # Tenure badges (mutually exclusive — award highest tier only)
    if registration_date is not None:
        delta_days = (now - registration_date).days
        if delta_days >= 10 * 365 + 3:   # ≥ 10 years (3653 days accounts for leap years)
            badges.append(REGISTRY["decade"])
        elif delta_days >= 5 * 365 + 2:  # ≥ 5 years (1827 days)
            badges.append(REGISTRY["veteran"])

    # Edit count badges (non-exclusive — award all tiers reached)
    if total_edits is not None:
        if total_edits >= 100_000:
            badges.append(REGISTRY["prolific_100k"])
        elif total_edits >= 50_000:
            badges.append(REGISTRY["prolific_50k"])
        elif total_edits >= 10_000:
            badges.append(REGISTRY["prolific_10k"])

    # Cross-wiki
    if wiki_count >= 3:
        badges.append(REGISTRY["multilingual"])

    # Commons
    if commons_edits and commons_edits > 0:
        badges.append(REGISTRY["commons_contributor"])
        # Visual historian: rough proxy via commons edit count
        # (actual upload count would need a separate replica query)
        if commons_edits >= 100:
            badges.append(REGISTRY["visual_historian"])

    # Wikidata
    if wikidata_edits and wikidata_edits > 0:
        badges.append(REGISTRY["wikidata_contributor"])

    return badges


def get_selected_badges(selected_ids: list[str]) -> list[AchievementBadge]:
    """
    Return AchievementBadge objects for the given list of accepted IDs.
    IDs not found in the registry are silently skipped (registry may change
    between gather runs; stale IDs should not crash the render).
    """
    return [REGISTRY[aid] for aid in selected_ids if aid in REGISTRY]
