"""
gather.py — Background data-gathering job for a single user.

Public API:
    run_gather_job(username, app, access_token, access_secret)
        Entry point called by the coordinator thread.
        Runs the full gather pipeline inside an app context.
        Updates UserProfile.gather_progress at each step.
        On success: sets gather_status='done', clears gather_error.
        On failure: sets gather_status='error', stores gather_error.

    sanitise_signature(html_str)  → str
        Sanitise signature HTML with nh3. Re-exported so app.py can call it
        at OAuth time without importing nh3 directly.

Pipeline steps and progress:
    0–5    Clear GatherCache
    5–15   Fetch global account info + per-wiki edit counts (replicas)
    15–20  Compute qualifying wikis
    20–35  Fetch avatar candidates (userpage images, Commons category, Wikidata P18)
    35–55  Fetch userpages + talkpages + subpage wikitext
    55–70  Detect barnstars
    70–85  Run LLM extraction (if llm_consent is True)
    85–95  Fetch user rights for qualifying wikis
    95–100 Write GatherCache, update UserProfile, commit

Signature HTML is fetched at OAuth time (in app.py /oauth-callback), not here.
All other steps are unauthenticated public API or replica DB calls.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import nh3
from flask import Flask

from src.db import GatherCache, UserProfile, db
from src.utils import utcnow

log = logging.getLogger(__name__)

# ── Signature sanitisation ────────────────────────────────────────────────────
# Re-exported here so app.py can call sanitise_signature() without knowing
# which library is used.

_SIG_TAGS = {'a', 'b', 'i', 'span', 'sup', 'sub', 'abbr'}
_SIG_ATTRS: dict[str, set[str]] = {
    'a':    {'href', 'title'},
    'span': {'style'},
    'abbr': {'title'},
}


def sanitise_signature(html_str: str) -> str:
    """Sanitise signature HTML. Returns empty string on error."""
    try:
        return nh3.clean(
            html_str or '',
            tags=_SIG_TAGS,
            attributes=_SIG_ATTRS,
            strip_comments=True,
        )
    except Exception:
        return ''


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_progress(app: Flask, username: str, progress: int) -> None:
    """Update gather_progress in the DB. Swallows errors to avoid aborting gather."""
    try:
        with app.app_context():
            profile = UserProfile.query.filter_by(username=username).first()
            if profile:
                profile.gather_progress = progress
                db.session.commit()
    except Exception as exc:
        log.debug('Could not update progress for %s: %s', username, exc)
    finally:
        db.session.remove()


def _cache_items(
    user_id: int,
    section: str,
    item_type: str,
    items: list[dict[str, Any]],
    source: str,
) -> list[GatherCache]:
    """Build GatherCache rows; each item becomes one row."""
    now = utcnow()
    rows = []
    for item in items:
        try:
            payload = json.dumps(item, ensure_ascii=True)
        except (TypeError, ValueError):
            continue
        rows.append(GatherCache(
            user_id=user_id,
            section=section,
            item_type=item_type,
            payload=payload,
            source=source,
            gathered_at=now,
        ))
    return rows


# ── Main gather logic ─────────────────────────────────────────────────────────

def run_gather_job(
    username: str,
    app: Flask,
    access_token: str | None = None,
    access_secret: str | None = None,
) -> None:
    """
    Run the full gather pipeline for `username`.

    Called by the coordinator thread. Runs entirely inside an app context.
    On success: sets gather_status='done'.
    On failure: sets gather_status='error', stores gather_error.
    Caller (coordinator) reads the future exception to update GatherQueue status.
    """
    with app.app_context():
        try:
            _run(app, username)
        except Exception as exc:
            log.error('Gather failed for %s: %s', username, exc, exc_info=True)
            try:
                profile = UserProfile.query.filter_by(username=username).first()
                if profile:
                    profile.gather_status = 'error'
                    profile.gather_error  = str(exc)[:500]
                    profile.last_gathered_at = utcnow()
                    db.session.commit()
            except Exception:
                db.session.rollback()
            raise
        finally:
            db.session.remove()


def _run(app: Flask, username: str) -> None:
    """Inner gather logic. Runs inside an existing app context."""
    from src import wiki as wiki_mod
    from src import replicas
    from src.barnstars import detect_barnstars
    from src.achievements import eligible_achievements

    home_wiki_url = app.config['HOME_WIKI_URL']
    home_wiki     = app.config['HOME_WIKI']
    max_wikis     = app.config['GATHER_MAX_QUALIFYING_WIKIS']

    def _step(n: int, label: str) -> None:
        log.warning('Gather [%s] step %d: %s', username, n, label)

    # ── Step 0: resolve profile ───────────────────────────────────────────────
    profile = UserProfile.query.filter_by(username=username).first()
    if profile is None:
        raise RuntimeError(f'No profile found for {username!r}')

    user_id = profile.id

    _step(1, 'clear cache')
    # ── Step 1: clear cache (0 → 5%) ─────────────────────────────────────────
    GatherCache.query.filter_by(user_id=user_id).delete()
    db.session.commit()
    profile.gather_progress = 5
    db.session.commit()

    def _flush(rows: list[GatherCache]) -> None:
        """Write rows to DB immediately and commit."""
        if rows:
            db.session.bulk_save_objects(rows)
            db.session.commit()

    # Skin and gender are fetched at OAuth time and stored on the profile.
    # Emit them here as userbox suggestions so they appear in the Infobox tab.
    _SKIN_DISPLAY: dict[str, str] = {
        'vector':       'Vector (legacy)',
        'vector-2022':  'Vector (2022)',
        'monobook':     'Monobook',
        'timeless':     'Timeless',
        'minerva':      'Minerva Neue',
        'cologne-blue': 'Cologne Blue',
        'modern':       'Modern',
    }
    pref_userboxes: list[dict[str, str]] = []
    if profile.wiki_skin:
        display = _SKIN_DISPLAY.get(profile.wiki_skin, profile.wiki_skin.capitalize())
        pref_userboxes.append({'icon': '🎨', 'label': f'This user uses the {display} skin'})
    if profile.wiki_gender == 'male':
        pref_userboxes.append({'icon': '♂', 'label': 'This user prefers to be described as male'})
    elif profile.wiki_gender == 'female':
        pref_userboxes.append({'icon': '♀', 'label': 'This user prefers to be described as female'})
    _flush(_cache_items(user_id, 'identity', 'userbox', pref_userboxes, source='api'))

    # Mark as running now — pref_userboxes are in the DB so the buffet page
    # shows something useful the moment gather.js redirects the user here.
    profile.gather_status = 'running'
    db.session.commit()

    _step(2, 'global account info + edit counts')
    # ── Step 2: global account info + per-wiki edit counts (5 → 15%) ─────────
    # Single API call to meta.wikimedia.org — returns everything at once.
    try:
        global_info, wiki_edit_counts = replicas.get_global_user_info_and_edit_counts(username)
    except replicas.ReplicaUnavailableError:
        global_info      = None
        wiki_edit_counts = {}

    registration_date = global_info['registration_date'] if global_info else None
    global_edit_count = global_info['global_edit_count'] if global_info else None

    # Update infobox fields
    if registration_date is not None:
        profile.registration_date = registration_date
    if global_edit_count is not None:
        profile.total_edits = global_edit_count

    # Set home_wiki to the user's most-edited wiki (only if not already overridden
    # by the user in the buffet — detected value always wins on fresh gather).
    if wiki_edit_counts:
        detected_home = max(wiki_edit_counts, key=lambda k: wiki_edit_counts[k])
        profile.home_wiki = detected_home

    profile.gather_progress = 15
    db.session.commit()

    _step(3, f'qualifying wikis from {len(wiki_edit_counts)} wikis')
    # ── Step 3: compute qualifying wikis (15 → 20%) ───────────────────────────
    home_edits = wiki_edit_counts.get(home_wiki, 0)
    threshold  = home_edits * 0.75 if home_edits else 0

    # Always include HOME_WIKI; add others at or above the 75% threshold
    qualifying_wikis: list[str] = [home_wiki]
    for dbname, count in sorted(wiki_edit_counts.items(), key=lambda x: -x[1]):
        if dbname == home_wiki:
            continue
        if count >= threshold:
            qualifying_wikis.append(dbname)
        if len(qualifying_wikis) >= max_wikis:
            break

    profile.gather_progress = 20
    db.session.commit()

    _step(4, f'avatar candidates across {len(qualifying_wikis)} qualifying wikis')
    # ── Step 4: avatar candidates (20 → 35%) ─────────────────────────────────
    avatar_filenames: list[str] = []

    for dbname in qualifying_wikis:
        wiki_url = wiki_mod.dbname_to_url(dbname)
        if not wiki_url:
            continue
        images = wiki_mod.get_userpage_images(wiki_url, username)
        avatar_filenames.extend(images)

    # Commons category search
    category_images = wiki_mod.get_commons_category_images(username)
    avatar_filenames.extend(category_images)

    # Wikidata P18
    p18 = wiki_mod.get_wikidata_p18(home_wiki_url, username)
    if p18:
        avatar_filenames.append(p18)

    # Deduplicate while preserving order
    seen_avatars: set[str] = set()
    unique_avatars: list[str] = []
    for fn in avatar_filenames:
        if fn and fn not in seen_avatars:
            seen_avatars.add(fn)
            unique_avatars.append(fn)

    # Keep avatars in memory until after barnstar detection — barnstar images
    # need to be filtered out before writing to avoid showing them as avatar options.
    avatar_rows = _cache_items(
        user_id, 'identity', 'avatar',
        [{'filename': fn} for fn in unique_avatars],
        source='api',
    )

    profile.gather_progress = 35
    db.session.commit()

    _step(5, 'fetch userpages + talkpages + subpages')
    # ── Step 5: fetch userpages + talkpages + subpages (35 → 55%) ────────────
    # wikitext_by_wiki: {dbname: {'userpage': str, 'talkpage': str, 'subpages': [str]}}
    wikitext_by_wiki: dict[str, dict[str, Any]] = {}

    for dbname in qualifying_wikis:
        wiki_url = wiki_mod.dbname_to_url(dbname)
        if not wiki_url:
            continue
        userpage = wiki_mod.get_userpage_wikitext(wiki_url, username)
        talkpage = wiki_mod.get_talkpage_wikitext(wiki_url, username)
        subpage_titles = wiki_mod.get_talk_subpage_titles(wiki_url, username)
        subpages = [
            wiki_mod.get_wikitext(wiki_url, title)
            for title in subpage_titles
        ]
        wikitext_by_wiki[dbname] = {
            'userpage':  userpage,
            'talkpage':  talkpage,
            'subpages':  subpages,
            'wiki_url':  wiki_url,
        }

    profile.gather_progress = 55
    db.session.commit()

    _step(6, 'barnstar detection')
    # ── Step 6: barnstar detection (55 → 70%) ────────────────────────────────
    all_wikitext_pages: list[str] = []
    for entry in wikitext_by_wiki.values():
        all_wikitext_pages.append(entry['userpage'])
        all_wikitext_pages.append(entry['talkpage'])
        all_wikitext_pages.extend(entry['subpages'])

    barnstars = detect_barnstars(all_wikitext_pages)

    # Remove barnstar images from avatar candidates — userpage image scraping
    # picks up all embedded images, including barnstar icons.
    barnstar_filenames = {bs['filename'] for bs in barnstars if bs.get('filename')}
    avatar_rows = [
        row for row in avatar_rows
        if json.loads(row.payload).get('filename') not in barnstar_filenames
    ]

    # Write avatars and barnstars together now that the filter is applied.
    _flush(avatar_rows)
    _flush(_cache_items(user_id, 'achievements', 'barnstar', barnstars, source='api'))

    profile.gather_progress = 70
    db.session.commit()

    _step(7, 'LLM extraction')
    # ── Step 7: LLM extraction (70 → 85%) ────────────────────────────────────
    if profile.llm_consent is True:
        from src.llm import extract_userbox_suggestions, extract_proud_of_suggestions

        for dbname, entry in wikitext_by_wiki.items():
            wikitext = entry['userpage']
            if not wikitext:
                continue

            userbox_suggestions = extract_userbox_suggestions(wikitext)
            _flush(_cache_items(
                user_id, 'identity', 'userbox',
                userbox_suggestions, source='llm',
            ))

            proud_of_suggestions = extract_proud_of_suggestions(wikitext)
            # Tag each suggestion with the wiki it was extracted from so the
            # card renderer can add the appropriate interwiki prefix.
            for item in proud_of_suggestions:
                item.setdefault('wiki', dbname)
            _flush(_cache_items(
                user_id, 'achievements', 'proud_of',
                proud_of_suggestions, source='llm',
            ))

    profile.gather_progress = 85
    db.session.commit()

    _step(8, 'user rights')
    # ── Step 8: user rights for qualifying wikis (85 → 95%) ──────────────────
    all_rights: list[str] = []
    for dbname in qualifying_wikis:
        wiki_url = wiki_mod.dbname_to_url(dbname)
        if not wiki_url:
            continue
        rights = wiki_mod.get_user_rights(wiki_url, username)
        all_rights.extend(rights)

    # Deduplicate
    unique_rights: list[str] = list(dict.fromkeys(all_rights))

    # Persist to UserProfile (JSON list)
    profile.user_rights = json.dumps(unique_rights, ensure_ascii=False)

    # Compute and store eligible achievements
    wiki_count = len(wiki_edit_counts)
    commons_edits = wiki_edit_counts.get('commonswiki', 0)
    wikidata_edits = wiki_edit_counts.get('wikidatawiki', 0)

    badges = eligible_achievements(
        registration_date=registration_date,
        total_edits=global_edit_count or 0,
        wiki_count=wiki_count,
        commons_edits=commons_edits,
        wikidata_edits=wikidata_edits,
    )
    _flush(_cache_items(
        user_id, 'achievements', 'badge',
        [{'id': b.id, 'name': b.name, 'icon': b.icon, 'color_class': b.color_class}
         for b in badges],
        source='api',
    ))

    profile.gather_progress = 95
    db.session.commit()

    _step(9, 'finalise')
    # ── Step 9: finalise (95 → 100%) ─────────────────────────────────────────
    profile.gather_status    = 'done'
    profile.gather_error     = None
    profile.last_gathered_at = utcnow()
    profile.gather_progress  = 100
    db.session.commit()
