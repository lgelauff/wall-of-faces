"""
barnstars.py — Dutch Wikipedia barnstar registry + wikitext detection.

Public API:
    detect_barnstars(wikitext_pages)  → list[dict]
        Given a list of wikitext strings (userpage + talk pages),
        return a deduplicated list of {'filename': str, 'name': str}
        for each barnstar found.

    REGISTRY  →  dict[str, str]
        Lowercase Commons filename → display name.
        Exported for use in gather.py (buffet suggestions).

Detection method:
    Scan each wikitext for 'Bestand:<filename>' or 'File:<filename>' occurrences
    (case-insensitive prefix, case-insensitive filename match against the registry).
    Returns at most MAX_BARNSTARS unique entries in registry-insertion order.
"""

from __future__ import annotations

import re

# Maximum number of barnstars to return (matches UserProfile.barnstars max 2)
MAX_BARNSTARS = 2

# ── Registry ──────────────────────────────────────────────────────────────────
#
# Keys: lowercase Commons filename (without 'File:' prefix).
# Values: display name shown on the card.
#
# Source: Dutch Wikipedia barnstar template list + commons:Category:Barnstars
# Only barnstars with a stable Commons filename and meaningful display name are
# included. Filenames that change frequently are intentionally omitted.

REGISTRY: dict[str, str] = {
    # ── General barnstars ────────────────────────────────────────────────────
    'original barnstar.png':               'Ster van verdienste',
    'original barnstar hires.png':         'Ster van verdienste',
    'the original barnstar.png':           'Ster van verdienste',
    'the barnstar of diligence.png':       'Ster van toewijding',
    'the tireless contributor barnstar.png': 'Ster van de onvermoeibare bijdrager',
    'the random acts of kindness barnstar.png': 'Ster van vriendelijkheid',
    'the civility barnstar.png':           'Ster van beleefdheid',
    'the epic barnstar.png':               'Epische ster',
    'the brilliant idea barnstar.png':     'Ster van het briljante idee',
    'the special barnstar.png':            'Bijzondere ster',
    'wikignome award.svg':                 'Wikignoom-prijs',
    'wikignome award 2.svg':               'Wikignoom-prijs',

    # ── Content barnstars ────────────────────────────────────────────────────
    'writers_barnstar_hires.png':          'Schrijversster',
    'the writers barnstar.png':            'Schrijversster',
    'the copyeditor\'s barnstar.png':      'Redacteursster',
    'the copyeditors barnstar.png':        'Redacteursster',
    'the citation barnstar.png':           'Bronnenster',
    'the reference desk barnstar.png':     'Informatiebaliesters',
    'the article rescue squadron barnstar.png': 'Reddingsster',
    'the working wikipedian\'s barnstar.png': 'Werkende Wikipedianer-ster',

    # ── Technical / media barnstars ──────────────────────────────────────────
    'the photographer\'s barnstar.png':    'Fotografenster',
    'the photographers barnstar.png':      'Fotografenster',
    'the image barnstar.png':              'Afbeeldingenster',
    'the graphic designer\'s barnstar.png': 'Grafisch-ontwerperster',
    'the technical barnstar.png':          'Technische ster',
    'the template barnstar.png':           'Sjabloonster',
    'the graphics barnstar.png':           'Grafische ster',

    # ── Community / admin barnstars ──────────────────────────────────────────
    'the anti-vandalism barnstar.png':     'Anti-vandalisme ster',
    'the guardian barnstar.png':           'Bewakersster',
    'the mediator barnstar.png':           'Bemiddelaarster',
    'the resilient barnstar.png':          'Veerkrachtige ster',
    'admin mop.png':                       'Beheerdersprijs',
    'the administrator\'s barnstar.png':   'Beheerdersprijs',

    # ── Dutch-Wikipedia-specific ─────────────────────────────────────────────
    'nl ster.png':                         'NL-ster',
    'nl barnstar.png':                     'Nederlandse Wikipedia-ster',
    'medal article quality.png':           'Medaille kwaliteitsartikel',
    'etoile.png':                          'Ster',

    # ── Wikimedia sister projects ────────────────────────────────────────────
    'wikimedia community logo.svg':        'Wikimediagemeenschapsprijs',
    'commons-logo.svg':                    'Commons-bijdrageprijs',
    'wikidata-logo.svg':                   'Wikidata-bijdrageprijs',
}

# Regex to match [[Bestand:<filename>]] or [[File:<filename>]] or bare
# transclusions like {{Bestand:<filename>}}. Captures just the filename part.
_FILE_RE = re.compile(
    r'(?:Bestand|File|Image)\s*:\s*([^\|\[\]{}\n]+)',
    re.IGNORECASE,
)


# ── Public API ────────────────────────────────────────────────────────────────

def detect_barnstars(wikitext_pages: list[str]) -> list[dict[str, str]]:
    """
    Scan a list of wikitext strings and return barnstar matches.

    Returns a list of {'filename': str, 'name': str} dicts, deduplicated
    by filename, in registry order, capped at MAX_BARNSTARS.

    Args:
        wikitext_pages: Raw wikitext strings to scan, typically
            ``[userpage, talkpage, *talk_subpages]``. Empty strings are ignored.
    """
    seen: set[str] = set()
    results: list[dict[str, str]] = []

    for wikitext in wikitext_pages:
        if not wikitext or not isinstance(wikitext, str):
            continue
        for match in _FILE_RE.finditer(wikitext):
            raw = match.group(1).strip()
            # Normalise: strip any caption/options after | or #
            filename = raw.split('|')[0].split('#')[0].strip()
            key = filename.lower()
            if key in seen:
                continue
            if key in REGISTRY:
                seen.add(key)
                results.append({'filename': filename, 'name': REGISTRY[key]})
                if len(results) >= MAX_BARNSTARS:
                    return results

    return results
