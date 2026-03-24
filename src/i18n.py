"""
i18n.py — UI string loading.

Loads strings/<UI_LANGUAGE>.yml once at startup.
Templates call {{ t('key') }} via a Jinja2 context processor injected in app.py.
Missing keys fall back to the key itself, so a partially-translated file degrades
gracefully.

Usage in app.py:
    from src.i18n import load_strings, make_t

    strings = load_strings(app.config['UI_LANGUAGE'], app.root_path)

    @app.context_processor
    def inject_t():
        return {'t': make_t(strings)}

Usage in render.py (PDF path, no Jinja2 context processor):
    from src.i18n import get_card_strings
    card_strings = get_card_strings()  # returns a SimpleNamespace
"""

import os
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import yaml


def load_strings(language: str, root_path: str) -> dict[str, str]:
    """Load a YAML string file and return a flat key-value dict.

    Falls back to ``en.yml`` when the requested language file is missing.

    Args:
        language: ISO language code, e.g. ``'nl'`` or ``'en'``.
        root_path: Absolute path to the Flask application root
            (``app.root_path``).

    Returns:
        Flat dict mapping translation keys to translated strings.
    """
    path = os.path.join(root_path, 'strings', f'{language}.yml')
    if not os.path.exists(path):
        # Why: graceful degradation — an untranslated deployment still works
        path = os.path.join(root_path, 'strings', 'en.yml')
    with open(path, encoding='utf-8') as f:
        data: dict[str, str] = yaml.safe_load(f) or {}
        return data


def make_t(strings: dict[str, str]) -> Callable[..., str]:
    """Return a translator function that resolves keys against *strings*.

    The returned ``t(key, **kwargs)`` function:

    - Looks up *key* in the dict; falls back to the key itself if missing,
      so partially-translated files degrade gracefully.
    - Applies ``str.format(**kwargs)`` for interpolation when kwargs are given.

    Args:
        strings: Flat translation dict as returned by :func:`load_strings`.

    Returns:
        A ``t(key, **kwargs) -> str`` callable suitable for Jinja2 contexts.
    """
    def t(key: str, **kwargs: Any) -> str:
        value: str = strings.get(key, key)
        if kwargs:
            try:
                value = value.format(**kwargs)
            except (KeyError, IndexError):
                # Why: a malformed translation string should not crash the page
                pass
        return value
    return t


def get_card_strings(strings: dict[str, str]) -> SimpleNamespace:
    """Extract card-specific strings into a dot-accessible namespace.

    Converts ``card.*`` keys from the translation dict into a
    :class:`~types.SimpleNamespace` so templates can write
    ``strings.member_since`` instead of ``t('card.member_since')``.

    Called by :mod:`src.render` when building the PDF template context.

    Args:
        strings: Flat translation dict as returned by :func:`load_strings`.

    Returns:
        SimpleNamespace with one attribute per card string.
    """
    def s(key: str) -> str:
        """Look up a card.* key, falling back to the bare key name."""
        return strings.get(f'card.{key}', key)

    return SimpleNamespace(
        user_prefix        = s('user_prefix'),
        member_since       = s('member_since'),
        home_base          = s('home_base'),
        edit_count         = s('edit_count'),
        rights             = s('rights'),
        proud_of           = s('proud_of'),
        achievements       = s('achievements'),
        biography_image_alt= s('biography_image_alt'),
        no_image           = s('no_image'),
    )
