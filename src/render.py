"""
render.py — Card rendering.

Exposes:
    render_card_html(profile, image_dir=None) -> str
        Render card_content.html to a complete, standalone HTML document.
        CSS is inlined so WeasyPrint needs no external fetches for styles.
        Image src= attributes are set to file:// paths from image_dir.

    render_card(profile, image_dir) -> bytes
        Render to PDF bytes.  Calls WeasyPrint via a subprocess so that
        safe_url_fetcher can be enforced (the python3 -m weasyprint CLI
        bypasses the custom fetcher).

    CardOverflowError
        Raised by render_card() when the PDF has more than one page,
        indicating card content exceeds the A5 boundary.

Both functions require an active Flask application context.

image_dir layout (populated by snapshot.py before render_card is called):
    <image_dir>/
        images/
            <commons_filename_1>
            <commons_filename_2>
            ...

For browser preview (no image_dir), image fields in the context are set to None
and the template renders placeholders.  The browser loads images via HTTPS using
/api/resolve-image — the PDF path never uses live URLs.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING

from flask import current_app, render_template
from pypdf import PdfReader

from src.achievements import get_selected_badges
from src.i18n import get_card_strings

if TYPE_CHECKING:
    # Avoid circular import at runtime; UserProfile is duck-typed below.
    pass


# ── Exception ─────────────────────────────────────────────────────────────────

class CardOverflowError(Exception):
    """Card content exceeds one A5 page after PDF rendering."""


# ── Month names for date formatting ───────────────────────────────────────────

_MONTH_NL = [
    '', 'januari', 'februari', 'maart', 'april', 'mei', 'juni',
    'juli', 'augustus', 'september', 'oktober', 'november', 'december',
]

_MONTH_EN = [
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]

_MONTH_NAMES = {
    'nl': _MONTH_NL,
    'en': _MONTH_EN,
}


def _format_date(dt: datetime | None, lang: str) -> str | None:
    """Format a date as '14 maart 2012' (Dutch) or '14 March 2012' (English)."""
    if dt is None:
        return None
    months = _MONTH_NAMES.get(lang, _MONTH_EN)
    return f"{dt.day}\u00a0{months[dt.month]}\u00a0{dt.year}"


def _format_edit_count(n: int | None) -> str | None:
    """Format 12847 → '12\u202f847' (narrow no-break space as thousands separator)."""
    if n is None:
        return None
    # Dutch convention: period as thousands separator; use narrow no-break space
    # to match the card's tight infobox cells.
    return f"{n:,}".replace(",", "\u202f")


# ── Image path helpers ────────────────────────────────────────────────────────

def _image_url(filename: str | None, image_dir: str | None) -> str | None:
    """
    Return a file:// URL for an image given its Commons filename and the
    snapshot image directory.  Returns None if filename or image_dir is absent.
    """
    if not filename or not image_dir:
        return None
    path = os.path.join(image_dir, 'images', filename)
    if not os.path.exists(path):
        return None
    # WeasyPrint requires three slashes: file:///absolute/path
    return 'file://' + path


# ── Context builder ───────────────────────────────────────────────────────────

def build_card_context(profile: Any, image_dir: str | None = None) -> dict[str, Any]:
    """
    Convert a UserProfile DB row into a template context dict for card_content.html.

    profile  — UserProfile ORM object (duck-typed; must have the fields below)
    image_dir — absolute path to the snapshot version directory, e.g.
                /data/project/wall-of-faces/snapshots/Username_42/20260501_143022_412381
                Set to None for browser preview (no local images).

    Fields read from profile:
        username, avatar_filename, home_base, biography,
        biography_image_filename, extra_sections (JSON str),
        userboxes (JSON str), barnstars (JSON str),
        selected_achievements (JSON str), proud_of (JSON str),
        signature_html, registration_date (datetime|None),
        total_edits (int|None), user_rights (JSON str|None)
    """
    from src.wiki import dbname_to_interwiki_prefix

    lang      = current_app.config.get('UI_LANGUAGE', 'nl')
    home_wiki = current_app.config.get('HOME_WIKI', '')
    strings   = current_app.extensions.get('strings', {})
    card_str  = get_card_strings(strings)

    # Parse JSON fields (stored as strings in the DB)
    def _json(field: str, default: Any) -> Any:
        raw = getattr(profile, field, None)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default

    userboxes_raw              = _json('userboxes', [])
    barnstars_raw              = _json('barnstars', [])
    selected_ids               = _json('selected_achievements', [])
    proud_of_raw               = _json('proud_of', [])
    extra_sections_raw         = _json('extra_sections', [])
    user_rights_raw            = _json('user_rights', [])
    content_image_filenames    = _json('content_image_filenames', [])

    # Userboxes: stored as {icon, label, bg, fg}
    # Template expects: {left_text, left_bg, left_color, right_text}
    userboxes = [
        SimpleNamespace(
            left_text  = ub.get('icon', ''),
            left_bg    = ub.get('bg', ''),
            left_color = ub.get('fg', ''),
            right_text = ub.get('label', ''),
        )
        for ub in userboxes_raw
    ]

    # Barnstars: stored as {filename, name}
    # Template expects: {name, image_url, gradient}
    barnstars = [
        SimpleNamespace(
            name      = bs.get('name', ''),
            image_url = _image_url(bs.get('filename'), image_dir),
            gradient  = bs.get('gradient', ''),
        )
        for bs in barnstars_raw
    ]

    # Achievements: convert accepted IDs → badge objects
    badges = get_selected_badges(selected_ids)
    achievements = [
        SimpleNamespace(
            name        = b.name,
            icon        = b.icon,
            color_class = b.color_class,
        )
        for b in badges
    ]

    # Proud-of: stored as {title, filename, wiki?}
    # display_title prefixes the title with an interwiki code when the item is
    # from a wiki other than HOME_WIKI (e.g. 'wikt:en:Binnenhof').
    def _proud_of_display_title(item: dict[str, Any]) -> str:
        title = item.get('title', '')
        wiki  = item.get('wiki') or home_wiki
        if wiki == home_wiki:
            return title
        prefix = dbname_to_interwiki_prefix(wiki, home_wiki)
        return f'{prefix}:{title}' if prefix else title

    proud_of = [
        SimpleNamespace(
            title         = item.get('title', ''),
            display_title = _proud_of_display_title(item),
            image_url     = _image_url(item.get('filename'), image_dir),
        )
        for item in proud_of_raw
    ]

    # Infobox user header: 'Gebruiker:Username' when on home wiki, else 'en:Username'
    user_home_wiki = getattr(profile, 'home_wiki', None) or home_wiki
    iw_prefix = dbname_to_interwiki_prefix(user_home_wiki, home_wiki)
    infobox_user_header = (
        f'{iw_prefix}:{profile.username}' if iw_prefix
        else f'{card_str.user_prefix}:{profile.username}'
    )

    # Profile view object — passed as `profile` to the template
    profile_view = SimpleNamespace(
        username             = profile.username,
        infobox_user_header  = infobox_user_header,
        avatar_url           = _image_url(
                                   getattr(profile, 'avatar_filename', None),
                                   image_dir,
                               ),
        home_base            = getattr(profile, 'home_base', None) or None,
        registration_date    = _format_date(
                                   getattr(profile, 'registration_date', None),
                                   lang,
                               ),
        edit_count           = getattr(profile, 'total_edits', None),
        user_rights          = user_rights_raw,
        userboxes            = userboxes,
        biography            = getattr(profile, 'biography', None) or None,
        biography_image_url  = _image_url(
                                   getattr(profile, 'biography_image_filename', None),
                                   image_dir,
                               ),
        content_image_urls   = [
                                   u for fn in content_image_filenames
                                   if (u := _image_url(fn, image_dir))
                               ],
        extra_sections       = extra_sections_raw,
        proud_of             = proud_of,
        barnstars            = barnstars,
        achievements         = achievements,
        signature_html       = getattr(profile, 'signature_html', None),
    )

    return dict(
        profile        = profile_view,
        strings        = card_str,
        event_name     = current_app.config.get('EVENT_NAME', 'Wall of Faces'),
        home_wiki_label= current_app.config.get('HOME_WIKI_LABEL',
                             current_app.config.get('HOME_WIKI_URL', '')),
        card_theme     = current_app.config.get('CARD_THEME', 'wikipedia'),
    )


# ── HTML renderer ─────────────────────────────────────────────────────────────

def _read_static(relative_path: str) -> str:
    """Read a file from static/ and return its contents."""
    path = os.path.join(current_app.root_path, 'static', relative_path)
    with open(path, encoding='utf-8') as f:
        return f.read()


def render_card_html(profile: Any, image_dir: str | None = None) -> str:
    """
    Render card_content.html to a complete, standalone HTML document.

    CSS (theme + print) is embedded inline so WeasyPrint does not need to
    fetch any stylesheet URLs.  Image src= values are file:// paths (PDF mode)
    or None (browser preview — placeholders are shown).

    Requires an active Flask application context.
    """
    ctx       = build_card_context(profile, image_dir)
    card_html = render_template('card_content.html', **ctx)

    theme     = current_app.config.get('CARD_THEME', 'wikipedia')
    lang      = current_app.config.get('UI_LANGUAGE', 'nl')
    theme_css = _read_static(f'css/card-theme-{theme}.css')
    print_css = _read_static('css/card-print.css')

    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="{lang}">\n'
        f'<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<style>\n{theme_css}\n</style>\n'
        f'<style>\n{print_css}\n</style>\n'
        f'</head>\n'
        f'<body>\n'
        f'{card_html}\n'
        f'</body>\n'
        f'</html>\n'
    )


# ── PDF renderer ──────────────────────────────────────────────────────────────

def render_card(profile: Any, image_dir: str) -> bytes:
    """
    Render the card to PDF bytes.

    Calls src/weasyprint_worker.py as a subprocess so that safe_url_fetcher
    is enforced.  (python3 -m weasyprint bypasses a custom url_fetcher.)

    Raises:
        CardOverflowError   — card content is more than one A5 page
        subprocess.CalledProcessError — WeasyPrint exited non-zero
        subprocess.TimeoutExpired     — render exceeded 60 seconds
    """
    html = render_card_html(profile, image_dir)

    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = os.path.join(tmpdir, 'card.html')
        pdf_path  = os.path.join(tmpdir, 'card.pdf')

        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)

        worker = os.path.join(current_app.root_path, 'src', 'weasyprint_worker.py')

        # sys.executable is the uWSGI binary when running under uWSGI, not Python.
        # Find the Python that has WeasyPrint installed by walking up from its
        # __file__: .../venv/lib/python3.x/site-packages/weasyprint/__init__.py
        # → 5 parents up → venv root → bin/python3.
        import weasyprint as _wp
        import pathlib
        _venv = pathlib.Path(_wp.__file__).parents[4]
        _venv_python = next(
            (str(_venv / 'bin' / n) for n in ('python3', 'python')
             if (_venv / 'bin' / n).is_file()),
            shutil.which('python3') or sys.executable,
        )

        try:
            subprocess.run(
                [_venv_python, worker, html_path, pdf_path],
                timeout=120,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors='replace').strip()
            current_app.logger.warning(
                'WeasyPrint worker failed (exe=%s): %s', _venv_python, stderr,
            )
            raise RuntimeError(
                f'WeasyPrint failed (exe={_venv_python}): {stderr}'
            ) from exc
        except subprocess.TimeoutExpired:
            raise CardOverflowError('PDF render timed out after 120s')

        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

    # Overflow check: WeasyPrint silently spills to a second page rather
    # than raising an error.  Detect and reject.
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) > 1:
        raise CardOverflowError(
            f'Card content exceeds A5 — PDF has {len(reader.pages)} pages. '
            'Trim content and try again.'
        )

    return pdf_bytes
