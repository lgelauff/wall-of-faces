"""
llm.py — Mistral AI integration for userbox and proud_of extraction.

Public API:
    extract_userbox_suggestions(wikitext)  → list[dict]
        Returns [{'icon': str, 'label': str}, ...] from wikitext.

    extract_proud_of_suggestions(wikitext) → list[dict]
        Returns [{'title': str, 'type': str}, ...] from wikitext.

Both functions:
  - Truncate wikitext to MISTRAL_MAX_WIKITEXT_CHARS before sending.
  - Return an empty list on any error (network, timeout, malformed JSON).
  - Validate output strictly: non-array, missing keys, oversized values → [].
  - Call the EU endpoint (api.eu.mistral.ai) as required by the privacy statement.

Called only from gather.py, and only when profile.llm_consent is True.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# Maximum wikitext characters sent to Mistral per call
MISTRAL_MAX_WIKITEXT_CHARS = 50_000

# Mistral model — small is sufficient for structured extraction
_MODEL = 'mistral-small-latest'

# Field length limits for output validation
_MAX_ICON_LEN = 20
_MAX_LABEL_LEN = 120
_MAX_TITLE_LEN = 300
_VALID_TYPES = {'article', 'image', 'other'}

# Maximum items per response (guard against pathological LLM output)
_MAX_ITEMS = 30


# ── Prompts ───────────────────────────────────────────────────────────────────

_USERBOX_PROMPT = """\
Given the following Wikipedia userpage wikitext, extract facts about this user \
that could appear as a userbox — e.g. spoken languages, areas of interest, \
professional roles, tools they use, communities they belong to.

Return ONLY a JSON array of objects with exactly two string fields: "icon" and "label".
- "icon": a short label or emoji representing the category (max 20 characters).
- "label": a concise description of the fact (max 120 characters).
Do not invent facts not present in the text. If nothing can be extracted, return [].

<wikitext>
{wikitext}
</wikitext>"""

_PROUD_OF_PROMPT = """\
Given the following Wikipedia userpage wikitext, identify any Wikipedia articles, \
Commons images, or Wikimedia contributions that this user mentions with pride \
or presents as notable personal work.

Return ONLY a JSON array of objects with exactly two string fields: "title" and "type".
- "title": the article title or file name as written in the wikitext (max 300 characters).
- "type": one of "article", "image", or "other".
Only include items explicitly mentioned in the text. Do not invent. \
If nothing qualifies, return [].

<wikitext>
{wikitext}
</wikitext>"""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_client() -> Any:
    """
    Return a Mistral client configured for the EU endpoint.
    Import is deferred so the module loads even if mistralai is not installed.
    """
    from mistralai import Mistral
    from flask import current_app
    api_key = current_app.config['MISTRAL_API_KEY']
    return Mistral(api_key=api_key, server_url='https://api.eu.mistral.ai')


def _call_mistral(prompt: str) -> list[Any]:
    """
    Call Mistral with the given prompt and return the parsed JSON array.
    Returns [] on any error.
    """
    try:
        client = _get_client()
        response = client.chat.complete(
            model=_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.1,       # low temperature for deterministic extraction
            max_tokens=2048,
        )
        content = response.choices[0].message.content or ''
        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith('```'):
            content = content.split('\n', 1)[-1]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
        data = json.loads(content)
        if not isinstance(data, list):
            return []
        return data[:_MAX_ITEMS]
    except Exception as exc:
        log.debug('Mistral call failed: %s', exc)
        return []


def _truncate(wikitext: str) -> str:
    return wikitext[:MISTRAL_MAX_WIKITEXT_CHARS]


# ── Public API ────────────────────────────────────────────────────────────────

def extract_userbox_suggestions(wikitext: str) -> list[dict[str, str]]:
    """
    Extract userbox-equivalent facts from wikitext using Mistral.

    Returns a list of {'icon': str, 'label': str} dicts.
    Empty list on error or if nothing is found.
    Filters out items with missing, non-string, or oversized fields.
    """
    if not wikitext:
        return []

    prompt = _USERBOX_PROMPT.format(wikitext=_truncate(wikitext))
    raw = _call_mistral(prompt)

    results = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        icon  = item.get('icon', '')
        label = item.get('label', '')
        if not isinstance(icon, str) or not isinstance(label, str):
            continue
        icon  = icon.strip()[:_MAX_ICON_LEN]
        label = label.strip()[:_MAX_LABEL_LEN]
        if not label:
            continue
        results.append({'icon': icon, 'label': label})

    return results


def extract_proud_of_suggestions(wikitext: str) -> list[dict[str, str]]:
    """
    Extract proud_of candidates from wikitext using Mistral.

    Returns a list of {'title': str, 'type': str} dicts.
    'type' is validated to be one of: 'article', 'image', 'other'.
    Empty list on error or if nothing is found.
    """
    if not wikitext:
        return []

    prompt = _PROUD_OF_PROMPT.format(wikitext=_truncate(wikitext))
    raw = _call_mistral(prompt)

    results = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = item.get('title', '')
        ptype = item.get('type', 'other')
        if not isinstance(title, str) or not isinstance(ptype, str):
            continue
        title = title.strip()[:_MAX_TITLE_LEN]
        ptype = ptype.strip().lower()
        if ptype not in _VALID_TYPES:
            ptype = 'other'
        if not title:
            continue
        results.append({'title': title, 'type': ptype})

    return results
