"""
replicas.py — Global user data via the Wikimedia API.

Uses meta.wikimedia.org's globaluserinfo API to fetch registration date,
global edit count, and per-wiki edit counts in a **single HTTP request**.

This replaces the previous approach of querying the CentralAuth replica DB
(for wiki list) and then making one XTools API call per wiki (N sequential
requests). That approach was O(N) in the number of wikis a user is attached
to, causing gathers to take hours for active contributors with 100–200+ wikis.

Public API:
    get_global_user_info(username)   → dict | None
        registration_date, global_edit_count

    get_wiki_edit_counts(username)   → dict[str, int]
        wiki dbname → edit count for all wikis with > 0 edits
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests




# ── Exceptions ────────────────────────────────────────────────────────────────

class ReplicaUnavailableError(Exception):
    """Raised when the global user info API is inaccessible."""


# ── Shared HTTP session ───────────────────────────────────────────────────────

_USER_AGENT = (
    'WallOfFaces/1.0 (Toolforge tool profile-creator-nlwiki; '
    'https://profile-creator-nlwiki.toolforge.org)'
)
_META_API = 'https://meta.wikimedia.org/w/api.php'
_TIMEOUT   = 20   # seconds — single call, generous timeout


def _fetch_globaluserinfo(username: str) -> dict[str, Any]:
    """
    Call meta.wikimedia.org globaluserinfo and return the raw API dict.

    guiprop=editcount  — global edit count
    guiprop=merged     — list of wikis with per-wiki editcount

    Raises ReplicaUnavailableError on network or API error.
    """
    try:
        resp = requests.get(
            _META_API,
            params={
                'action':      'query',
                'meta':        'globaluserinfo',
                'guiprop':     'editcount|merged',
                'guiuser':     username,
                'format':      'json',
                'formatversion': '2',
            },
            headers={'User-Agent': _USER_AGENT},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get('query', {}).get('globaluserinfo', {})
    except requests.RequestException as exc:
        raise ReplicaUnavailableError(
            f'globaluserinfo API request failed: {exc}'
        ) from exc


# ── Public API ────────────────────────────────────────────────────────────────

def get_global_user_info(username: str) -> dict[str, Any] | None:
    """
    Fetch global account info via the Wikimedia API.

    Returns a dict with:
        registration_date  — datetime (UTC, tz-aware) or None
        global_edit_count  — int or None

    Returns None if the account does not exist.
    Raises ReplicaUnavailableError on network failure.
    """
    gui = _fetch_globaluserinfo(username)
    if not gui or 'missing' in gui:
        return None

    reg_dt: datetime | None = None
    reg_raw = gui.get('registration')
    if reg_raw:
        try:
            reg_dt = datetime.fromisoformat(reg_raw.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass

    return {
        'registration_date': reg_dt,
        'global_edit_count': gui.get('editcount'),
    }


def get_global_user_info_and_edit_counts(
    username: str,
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    """
    Fetch global account info AND per-wiki edit counts in a single API call.

    Returns (global_info, wiki_edit_counts) where global_info has the same
    shape as get_global_user_info() and wiki_edit_counts maps dbname → count.

    Prefer this over calling get_global_user_info + get_wiki_edit_counts
    separately to avoid making the same HTTP request twice.
    """
    gui = _fetch_globaluserinfo(username)
    if not gui or 'missing' in gui:
        return None, {}

    reg_dt: datetime | None = None
    reg_raw = gui.get('registration')
    if reg_raw:
        try:
            reg_dt = datetime.fromisoformat(reg_raw.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass

    global_info: dict[str, Any] = {
        'registration_date': reg_dt,
        'global_edit_count': gui.get('editcount'),
    }

    counts: dict[str, int] = {}
    for entry in gui.get('merged', []):
        wiki  = entry.get('wiki')
        count = entry.get('editcount', 0) or 0
        if wiki and count > 0:
            counts[wiki] = count

    return global_info, counts


def get_wiki_edit_counts(username: str) -> dict[str, int]:
    """
    Fetch per-wiki edit counts via the Wikimedia API.

    Returns a dict mapping wiki dbname → edit count for all wikis
    where the user has at least one edit.

    Uses the same globaluserinfo call as get_global_user_info — but since
    gather.py calls them separately we keep the interface unchanged and
    accept the small overhead of a second API call.

    Raises ReplicaUnavailableError on network failure.
    """
    gui = _fetch_globaluserinfo(username)
    if not gui or 'missing' in gui:
        return {}

    counts: dict[str, int] = {}
    for entry in gui.get('merged', []):
        wiki  = entry.get('wiki')
        count = entry.get('editcount', 0) or 0
        if wiki and count > 0:
            counts[wiki] = count

    return counts
