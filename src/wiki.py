"""
wiki.py — MediaWiki API calls.

All content (wikitext, images, user rights, signatures) comes from here.
All requests use a shared session with a consistent User-Agent.

Functions called by gather.py:
    get_user_rights(wiki_url, username)           → list[str]
    get_userpage_wikitext(wiki_url, username)     → str
    get_talkpage_wikitext(wiki_url, username)     → str
    get_talk_subpage_titles(wiki_url, username)   → list[str]
    get_wikitext(wiki_url, title)                 → str
    get_userpage_images(wiki_url, username)       → list[str]
    get_commons_category_images(username)         → list[str]
    get_commons_thumbnails(filenames, width)      → dict[str, str]
    get_wikidata_p18(wiki_url, username)          → str | None
    dbname_to_url(dbname)                         → str
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

# ── Shared session ────────────────────────────────────────────────────────────

_USER_AGENT = 'WallOfFaces/1.0 (Toolforge; https://nl.wikipedia.org)'
_COMMONS_URL = 'https://commons.wikimedia.org'
_WIKIDATA_URL = 'https://www.wikidata.org'

_session = requests.Session()
_session.headers.update({'User-Agent': _USER_AGENT})

_DEFAULT_TIMEOUT = 15   # seconds
_MAX_WIKITEXT_CHARS = 2 * 1024 * 1024   # ~2 MB cap on raw wikitext (character count)


# ── Wiki URL helpers ──────────────────────────────────────────────────────────

# Special-case wiki dbnames that don't follow the {lang}wiki → {lang}.wikipedia.org pattern
_SPECIAL_WIKIS: dict[str, str] = {
    'commonswiki':     'https://commons.wikimedia.org',
    'wikidatawiki':    'https://www.wikidata.org',
    'metawiki':        'https://meta.wikimedia.org',
    'mediawikiwiki':   'https://www.mediawiki.org',
    'specieswiki':     'https://species.wikimedia.org',
    'incubatorwiki':   'https://incubator.wikimedia.org',
    'foundationwiki':  'https://foundation.wikimedia.org',
}

# Wikisource, Wiktionary, etc. follow different URL patterns; add as needed.
_DBNAME_RE = re.compile(r'^([a-z]+)(wiktionary|wikisource|wikiquote|wikibooks|wikinews|wikiversity|wikivoyage)$')


def dbname_to_url(dbname: str) -> str | None:
    """
    Convert a wiki dbname to its base URL.

    Examples:
        'nlwiki'       → 'https://nl.wikipedia.org'
        'commonswiki'  → 'https://commons.wikimedia.org'
        'frwikisource' → 'https://fr.wikisource.org'

    Returns None for unrecognised patterns.
    """
    if dbname in _SPECIAL_WIKIS:
        return _SPECIAL_WIKIS[dbname]

    # {lang}wiki → {lang}.wikipedia.org
    if dbname.endswith('wiki') and not dbname.endswith('wikiwiki'):
        lang = dbname[:-4]
        if lang and lang.isalpha():
            return f'https://{lang}.wikipedia.org'

    # {lang}{project} → {lang}.{project_domain}.org
    m = _DBNAME_RE.match(dbname)
    if m:
        lang, project = m.groups()
        project_map = {
            'wikisource':   'wikisource',
            'wiktionary':   'wiktionary',
            'wikiquote':    'wikiquote',
            'wikibooks':    'wikibooks',
            'wikinews':     'wikinews',
            'wikiversity':  'wikiversity',
            'wikivoyage':   'wikivoyage',
        }
        for suffix, domain in project_map.items():
            if project == suffix:
                return f'https://{lang}.{domain}.org'

    return None


# Reverse map for url_to_dbname
_SPECIAL_WIKIS_BY_HOST: dict[str, str] = {
    v.replace('https://', ''): k for k, v in _SPECIAL_WIKIS.items()
}

_URL_PROJECT_SUFFIX: dict[str, str] = {
    'wikipedia':   'wiki',
    'wiktionary':  'wiktionary',
    'wikisource':  'wikisource',
    'wikiquote':   'wikiquote',
    'wikibooks':   'wikibooks',
    'wikinews':    'wikinews',
    'wikiversity': 'wikiversity',
    'wikivoyage':  'wikivoyage',
}


def url_to_dbname(url: str) -> str | None:
    """
    Convert a wiki URL (with or without scheme) to its dbname.

    Examples:
        'nl.wikipedia.org'              → 'nlwiki'
        'https://nl.wikipedia.org'      → 'nlwiki'
        'commons.wikimedia.org'         → 'commonswiki'
        'fr.wikisource.org'             → 'frwikisource'

    Returns None for unrecognised patterns.
    """
    host = url.lower().strip()
    host = host.removeprefix('https://').removeprefix('http://')
    host = host.rstrip('/').split('/')[0]   # strip any path

    if host in _SPECIAL_WIKIS_BY_HOST:
        return _SPECIAL_WIKIS_BY_HOST[host]

    # {lang}.{project}.org
    parts = host.split('.')
    if len(parts) == 3 and parts[2] == 'org':
        lang, project = parts[0], parts[1]
        if lang and project in _URL_PROJECT_SUFFIX:
            return lang + _URL_PROJECT_SUFFIX[project]

    return None


# Interwiki short-codes for projects when linking from a Wikipedia-based home wiki.
# Maps project suffix → canonical short prefix (wiktionary → wikt, etc.)
_INTERWIKI_PROJECT_PREFIX: dict[str, str] = {
    'wiktionary':  'wikt',
    'wikisource':  's',
    'wikiquote':   'q',
    'wikibooks':   'b',
    'wikinews':    'n',
    'wikiversity': 'v',
    'wikivoyage':  'voy',
}

_INTERWIKI_FIXED: dict[str, str] = {
    'commonswiki':   'commons',
    'wikidatawiki':  'wikidata',
    'metawiki':      'meta',
    'mediawikiwiki': 'mw',
    'specieswiki':   'species',
}


def dbname_to_interwiki_prefix(dbname: str, home_wiki: str) -> str:
    """Return the interwiki prefix used to link to *dbname* from *home_wiki*.

    Returns an empty string when dbname equals home_wiki (no prefix needed).

    Args:
        dbname:    Target wiki database name, e.g. ``'enwiki'`` or ``'enwiktionary'``.
        home_wiki: Source wiki database name, e.g. ``'nlwiki'``.

    Returns:
        Interwiki prefix string, e.g. ``'en'``, ``'wikt:en'``, ``'commons'``.
        Falls back to the dbname itself for unrecognised patterns.

    Examples (from nlwiki):
        ``'enwiki'``       → ``'en'``
        ``'commonswiki'``  → ``'commons'``
        ``'nlwiktionary'`` → ``'wikt'``   (same-language sister project)
        ``'enwiktionary'`` → ``'wikt:en'``
        ``'frwikisource'`` → ``'s:fr'``
    """
    if dbname == home_wiki:
        return ''

    if dbname in _INTERWIKI_FIXED:
        return _INTERWIKI_FIXED[dbname]

    # Derive home language for same-language project shortcuts (e.g. nlwiki → nl)
    home_lang = home_wiki[:-4] if (home_wiki.endswith('wiki') and not home_wiki.endswith('wikiwiki')) else ''

    # {lang}wiki → Wikipedia language code
    if dbname.endswith('wiki') and not dbname.endswith('wikiwiki'):
        lang = dbname[:-4]
        if lang and lang.isalpha():
            return lang

    # {lang}{project} → short prefix, with optional language qualifier
    m = _DBNAME_RE.match(dbname)
    if m:
        lang, project = m.group(1), m.group(2)
        prefix = _INTERWIKI_PROJECT_PREFIX.get(project, project)
        return prefix if lang == home_lang else f'{prefix}:{lang}'

    return dbname  # unknown pattern — use dbname as fallback


# ── Core API helper ───────────────────────────────────────────────────────────

def _api(wiki_url: str, params: dict[str, Any], retries: int = 2) -> dict[str, Any]:
    """
    Make a MediaWiki API GET request and return the parsed JSON.
    Retries on transient network errors (not on 4xx).
    """
    params.setdefault('format', 'json')
    params.setdefault('formatversion', '2')

    for attempt in range(retries + 1):
        try:
            resp = _session.get(
                f'{wiki_url}/w/api.php',
                params=params,
                timeout=_DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        except requests.RequestException as exc:
            if attempt == retries:
                raise
            time.sleep(1.5 ** attempt)   # 1.5s, 2.25s back-off
    return {}


# ── User rights ───────────────────────────────────────────────────────────────

def get_user_rights(wiki_url: str, username: str) -> list[str]:
    """
    Return the list of display-ready user right names for `username` on `wiki_url`.
    Returns an empty list if the user has no special rights or the call fails.
    """
    try:
        data = _api(wiki_url, {
            'action': 'query',
            'list':   'users',
            'ususers': username,
            'usprop': 'groups',
        })
        users = data.get('query', {}).get('users', [{}])
        groups = users[0].get('groups', []) if users else []
        # Filter out the universal groups every account has
        skip = {'*', 'user', 'autoconfirmed', 'emailconfirmed'}
        return [g for g in groups if g not in skip]
    except Exception:
        return []


# ── Wikitext fetching ─────────────────────────────────────────────────────────

def get_wikitext(wiki_url: str, title: str) -> str:
    """
    Fetch the raw wikitext of a page.
    Returns an empty string if the page doesn't exist or the call fails.
    """
    try:
        data = _api(wiki_url, {
            'action':  'query',
            'prop':    'revisions',
            'titles':  title,
            'rvprop':  'content',
            'rvslots': 'main',
        })
        pages = data.get('query', {}).get('pages', [])
        if not pages:
            return ''
        page = pages[0]
        if page.get('missing'):
            return ''
        slots = page.get('revisions', [{}])[0].get('slots', {})
        content = slots.get('main', {}).get('content', '')
        # Safety cap — should never exceed 2 M chars but guard anyway
        return str(content)[:_MAX_WIKITEXT_CHARS]
    except Exception:
        return ''


def get_userpage_wikitext(wiki_url: str, username: str) -> str:
    """Fetch the raw wikitext of User:username. Returns '' if missing or on error."""
    return get_wikitext(wiki_url, f'User:{username}')


def get_talkpage_wikitext(wiki_url: str, username: str) -> str:
    """Fetch the raw wikitext of User talk:username. Returns '' if missing or on error."""
    return get_wikitext(wiki_url, f'User talk:{username}')


def get_talk_subpage_titles(wiki_url: str, username: str) -> list[str]:
    """
    Return the titles of all subpages under User_talk:username.
    Used to discover archived barnstar-award talk page sections.
    """
    try:
        data = _api(wiki_url, {
            'action':      'query',
            'list':        'allpages',
            'apprefix':    f'{username}/',
            'apnamespace': '3',   # NS_USER_TALK = 3
            'aplimit':     '50',
        })
        return [
            p['title']
            for p in data.get('query', {}).get('allpages', [])
        ]
    except Exception:
        return []


# ── Userpage images (avatar candidates) ──────────────────────────────────────

def get_userpage_images(wiki_url: str, username: str) -> list[str]:
    """
    Return Commons filenames of images embedded on User:username.
    These are avatar candidates (source: api).
    """
    try:
        data = _api(wiki_url, {
            'action': 'query',
            'prop':   'images',
            'titles': f'User:{username}',
            'imlimit': '20',
        })
        pages = data.get('query', {}).get('pages', [])
        if not pages or pages[0].get('missing'):
            return []
        images = pages[0].get('images', [])
        # Strip 'File:' prefix and return Commons filenames
        return [
            img['title'].removeprefix('File:').removeprefix('Bestand:')
            for img in images
            if img.get('title', '').startswith(('File:', 'Bestand:'))
        ]
    except Exception:
        return []


def get_commons_category_images(username: str, limit: int = 10) -> list[str]:
    """
    Search for a 'Photographs by {username}' category on Commons and return
    up to `limit` image filenames from it.
    Returns an empty list if no category is found.
    """
    # Try common category name patterns
    candidates = [
        f'Photographs by {username}',
        f'Photos by {username}',
        f'Images by {username}',
        f'Files by {username}',
    ]
    for cat_name in candidates:
        try:
            data = _api(_COMMONS_URL, {
                'action':  'query',
                'list':    'categorymembers',
                'cmtitle': f'Category:{cat_name}',
                'cmtype':  'file',
                'cmlimit': str(limit),
            })
            members = data.get('query', {}).get('categorymembers', [])
            if members:
                return [
                    m['title'].removeprefix('File:')
                    for m in members
                    if m.get('title', '').startswith('File:')
                ]
        except Exception:
            continue
    return []


# ── Thumbnail URL cache ───────────────────────────────────────────────────────
#
# Two-layer cache for Commons thumbnail URLs:
#
#   1. In-process dict  (_thumb_cache) — zero-cost lookup within a process run.
#   2. Disk JSON file   (_THUMB_CACHE_PATH) — survives uWSGI restarts; loaded
#      once at first use, written after each batch of new entries.
#
# Barnstar filenames come from a finite registry (~30–50 entries). Once every
# known barnstar has been seen by at least one user, all subsequent gathers
# (re-gathers or new users with the same barnstars) never touch the Commons API.
#
# File location: $HOME/.cache/wof_thumb_cache.json  (writable on Toolforge NFS).
# Fallback: <this file's parent>/../.claude/wof_thumb_cache.json (local dev).

_THUMB_CACHE_PATH: Path = (
    Path(os.environ.get('HOME', '')) / '.cache' / 'wof_thumb_cache.json'
    if os.environ.get('HOME')
    else Path(__file__).parent.parent / '.claude' / 'wof_thumb_cache.json'
)

# In-memory mirror: key = "filename_lower:width", value = url
_thumb_cache: dict[str, str] = {}
_thumb_cache_loaded: bool = False


def _thumb_cache_key(filename: str, width: int) -> str:
    return f'{filename.lower()}:{width}'


def _load_thumb_cache() -> None:
    global _thumb_cache_loaded
    if _thumb_cache_loaded:
        return
    _thumb_cache_loaded = True
    try:
        if _THUMB_CACHE_PATH.exists():
            data = json.loads(_THUMB_CACHE_PATH.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                _thumb_cache.update(data)
    except Exception:
        pass  # corrupt or unreadable — start fresh


def _save_thumb_cache() -> None:
    try:
        _THUMB_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _THUMB_CACHE_PATH.write_text(
            json.dumps(_thumb_cache, ensure_ascii=True, indent=None),
            encoding='utf-8',
        )
    except Exception:
        pass  # non-fatal: cache will just miss on next restart


def get_commons_thumbnails(
    filenames: list[str],
    width: int = 200,
) -> dict[str, str]:
    """
    Fetch thumbnail URLs for a list of Commons filenames in a single API call.

    Returns a dict mapping filename → thumbnail URL.
    Filenames not found on Commons are omitted from the result.

    Two-layer cache (in-process dict + disk JSON) means:
    - Same filename at same width is never fetched twice within a process run.
    - Cache survives uWSGI restarts; barnstar URLs are stable and never expire.
    - Different users sharing the same barnstar image share the cached URL.

    At most 50 filenames per call (MediaWiki API limit).
    """
    if not filenames:
        return {}

    _load_thumb_cache()

    result: dict[str, str] = {}
    missing: list[str] = []

    for fn in filenames[:50]:
        key = _thumb_cache_key(fn, width)
        if key in _thumb_cache:
            result[fn] = _thumb_cache[key]
        else:
            missing.append(fn)

    if not missing:
        return result

    titles = '|'.join(f'File:{fn}' for fn in missing)
    try:
        data = _api(_COMMONS_URL, {
            'action':     'query',
            'prop':       'imageinfo',
            'iiprop':     'url',
            'iiurlwidth': str(width),
            'titles':     titles,
            'format':     'json',
            'formatversion': '2',
        })
    except Exception:
        return result  # return whatever we got from cache

    new_entries = False
    for page in data.get('query', {}).get('pages', []):
        if page.get('missing'):
            continue
        title = page.get('title', '')
        if not title.startswith('File:'):
            continue
        fn = title[len('File:'):]
        ii = page.get('imageinfo', [{}])[0]
        url = ii.get('thumburl') or ii.get('url')
        if url:
            _thumb_cache[_thumb_cache_key(fn, width)] = url
            result[fn] = url
            new_entries = True

    if new_entries:
        _save_thumb_cache()

    return result


def get_wikidata_p18(wiki_url: str, username: str) -> str | None:
    """
    Return the Commons filename of the P18 (image) property from the Wikidata
    item linked to User:username on wiki_url, or None if not found.
    """
    try:
        # Step 1: get the Wikidata item ID from the userpage
        data = _api(wiki_url, {
            'action':  'query',
            'prop':    'pageprops',
            'ppprop':  'wikibase_item',
            'titles':  f'User:{username}',
        })
        pages = data.get('query', {}).get('pages', [])
        if not pages:
            return None
        qid = pages[0].get('pageprops', {}).get('wikibase_item')
        if not qid:
            return None

        # Step 2: fetch P18 from Wikidata
        wd_data = _api(_WIKIDATA_URL, {
            'action': 'wbgetclaims',
            'entity': qid,
            'property': 'P18',
        })
        claims = wd_data.get('claims', {}).get('P18', [])
        if not claims:
            return None
        snak = claims[0].get('mainsnak', {}).get('datavalue', {}).get('value', {})
        return snak if isinstance(snak, str) else None
    except Exception:
        return None
