"""
snapshot.py — Card finalization and versioned snapshot management.

Public API:
    create_snapshot(profile, triggered_by_user) -> (version, datetime)
        Download images, render PDF, write versioned directory, update symlink.
        Raises CardOverflowError if the card exceeds A5.
        Called synchronously from POST /api/finalize.

    run_snapshot_job(username, app)
        Coordinator entry point for admin-triggered snapshots.
        Wraps create_snapshot with app context and error handling.

    prune_inactive_user(profile)
        Delete old snapshot versions for accounts inactive longer than
        SNAPSHOT_INACTIVE_PRUNE_DAYS. Keeps user_approved_snapshot_version
        and everything after it. No-op if user never explicitly finalized.

    safe_username_dir(username, user_id) -> str
        Return a filesystem-safe, globally-unique directory name.

    assert_safe_path(path) -> str
        Raise ValueError if the resolved path escapes SNAPSHOT_ROOT.

Snapshot directory layout:
    {SNAPSHOT_ROOT}/
      {safe_username_dir}/
        latest -> {version}          ← atomic symlink (updated on each snapshot)
        {version}/                   ← e.g. 20260501_143022_412381
          images/
            {commons_filename}       ← downloaded at print-quality width
            ...
          card.pdf                   ← rendered A5 PDF

On any exception during steps 3–8 of create_snapshot, the partial version
directory is removed before re-raising, so no orphaned files accumulate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import requests
from flask import Flask, current_app

from src.db import UserProfile, db
from src.render import CardOverflowError, render_card
from src.utils import utcnow

# ── Constants ─────────────────────────────────────────────────────────────────

VERSION_RE = re.compile(r'^\d{8}_\d{6}_\d+$')

# Only download images from the Wikimedia thumbnail CDN
_ALLOWED_IMAGE_HOST = 'upload.wikimedia.org'

# Thumbnail widths per image type (px) — balances print quality vs file size.
# At 300 dpi, A5 column width (~70mm) needs ~828px, so 400–600px is generous
# for a 35–60mm display width. Barnstars and proud_of thumbnails are small.
_WIDTHS = {
    'avatar':    400,
    'biography': 600,
    'barnstar':  150,
    'proud_of':  200,
}

_MW_USER_AGENT = 'WallOfFaces/1.0 (Toolforge; https://nl.wikipedia.org)'


# ── Path safety ───────────────────────────────────────────────────────────────

def safe_username_dir(username: str, user_id: int) -> str:
    """
    Return a filesystem-safe, globally-unique directory name for a user.

    Appending user_id ensures uniqueness even when two usernames produce the
    same cleaned string (e.g. 'User A' and 'User_A' both become 'User_A').
    Dots are excluded to avoid NFS and Python module path issues.
    """
    clean = re.sub(r'[^\w\-]', '_', username)
    if not clean or clean.startswith('_'):
        clean = 'user'
    return f'{clean}_{user_id}'


def assert_safe_path(path: str) -> str:
    """
    Resolve the path and raise ValueError if it escapes SNAPSHOT_ROOT.
    Returns the resolved absolute path string.
    """
    snapshot_root = current_app.config['SNAPSHOT_ROOT']
    resolved      = os.path.realpath(path)
    root_resolved = os.path.realpath(snapshot_root)
    if not resolved.startswith(root_resolved + os.sep):
        raise ValueError(f'Path escapes snapshot root: {path!r}')
    return resolved


# ── Image fetching ────────────────────────────────────────────────────────────

def _resolve_image_url(filename: str, width: int) -> str | None:
    """
    Call the Commons MediaWiki API to get a thumbnail URL at the given width.
    Returns the URL string, or None if the file is missing on Commons.
    """
    try:
        api_params: dict[str, Any] = {
            'action':     'query',
            'prop':       'imageinfo',
            'iiprop':     'url',
            'iiurlwidth': width,
            'titles':     f'File:{filename}',
            'format':     'json',
        }
        resp = requests.get(
            'https://commons.wikimedia.org/w/api.php',
            params=api_params,
            headers={'User-Agent': _MW_USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        pages: dict[str, Any] = resp.json().get('query', {}).get('pages', {})
        page: dict[str, Any]  = next(iter(pages.values()), {})
        if 'missing' in page:
            return None
        imageinfo: dict[str, Any] = page.get('imageinfo', [{}])[0]
        thumb: str | None = imageinfo.get('thumburl') or imageinfo.get('url')
        return thumb
    except Exception:
        return None


def _fetch_image(url: str, dest_path: str, _depth: int = 0) -> None:
    """
    Download an image from `url` to `dest_path`.

    Safety constraints:
      - Only https:// URLs allowed.
      - Only upload.wikimedia.org is an allowed host.
      - Maximum one redirect hop followed manually (allow_redirects=False).
      - 15-second timeout.
      - Maximum response size: 20 MB (protects against oversized originals).
    """
    if _depth > 1:
        raise ValueError('Too many redirects fetching image')

    parsed = urlparse(url)
    if parsed.scheme != 'https':
        raise ValueError(f'Only https allowed, got: {parsed.scheme!r}')
    if parsed.hostname != _ALLOWED_IMAGE_HOST:
        raise ValueError(f'Disallowed image host: {parsed.hostname!r}')

    max_bytes = 20 * 1024 * 1024  # 20 MB

    with requests.get(url, stream=True, timeout=15, allow_redirects=False) as resp:
        if resp.is_redirect:
            location = resp.headers.get('Location', '')
            _fetch_image(location, dest_path, _depth + 1)
            return
        resp.raise_for_status()

        received = 0
        with open(dest_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=65536):
                received += len(chunk)
                if received > max_bytes:
                    raise ValueError(f'Image exceeds 20 MB size limit: {url!r}')
                f.write(chunk)


# ── Core snapshot logic ───────────────────────────────────────────────────────

def create_snapshot(
    profile: UserProfile,
    triggered_by_user: bool = False,
) -> tuple[str, object]:
    """
    Create a versioned snapshot for the given UserProfile.

    Steps:
      1. Collect all Commons filenames from the profile.
      2. Generate a version string (timestamp with microseconds).
      3. Create version directory with 0o700 permissions.
      4. Resolve each filename → thumbnail URL via Commons API.
      5. Download each image to version/images/.
      6. Render PDF via WeasyPrint (render_card).
      7. Write PDF to version/card.pdf.
      8. Update 'latest' symlink atomically.
      9. Update UserProfile fields and commit.

    On any exception in steps 3–9: remove the partial version directory,
    then re-raise.

    Raises:
        CardOverflowError  — card content exceeds one A5 page.
        ValueError         — path safety violation.
    """
    snapshot_root = current_app.config['SNAPSHOT_ROOT']
    user_dir_name = safe_username_dir(profile.username, profile.id)
    user_dir      = os.path.join(snapshot_root, user_dir_name)

    # Create the user directory if it doesn't exist yet
    os.makedirs(user_dir, mode=0o700, exist_ok=True)
    # Verify it's inside SNAPSHOT_ROOT (paranoia — makedirs created it, but check anyway)
    assert_safe_path(user_dir)

    # Generate version string — microseconds make same-second collisions impossible
    version     = utcnow().strftime('%Y%m%d_%H%M%S_%f')
    version_dir = assert_safe_path(os.path.join(user_dir, version))
    images_dir  = os.path.join(version_dir, 'images')
    pdf_path    = os.path.join(version_dir, 'card.pdf')

    try:
        os.makedirs(images_dir, mode=0o700)

        # Collect filenames and their desired thumbnail widths
        to_download = _collect_images(profile)

        # Resolve + download each image
        for filename, width in to_download:
            if not filename:
                continue
            url = _resolve_image_url(filename, width)
            if url is None:
                continue   # file missing on Commons — placeholder shown in PDF
            dest = os.path.join(images_dir, os.path.basename(filename))
            try:
                _fetch_image(url, dest)
            except Exception as exc:
                current_app.logger.warning(
                    'Failed to download image %r for %s: %s',
                    filename, profile.username, exc,
                )
                # Continue — a missing image renders as a placeholder, not a crash

        # Render PDF (raises CardOverflowError if > 1 page)
        pdf_bytes = render_card(profile, image_dir=version_dir)

        with open(pdf_path, 'wb') as f:
            f.write(pdf_bytes)

        # Atomically update the 'latest' symlink
        tmp_link = os.path.join(user_dir, 'latest.tmp')
        if os.path.islink(tmp_link):
            os.unlink(tmp_link)
        os.symlink(version, tmp_link)
        os.replace(tmp_link, os.path.join(user_dir, 'latest'))

        # Persist snapshot metadata
        now = utcnow()
        profile.snapshot_at      = now
        profile.snapshot_version = version
        if triggered_by_user:
            profile.user_approved_snapshot_version = version
        db.session.commit()

        return version, now

    except Exception:
        # Clean up the partial version directory before re-raising
        if os.path.exists(version_dir):
            shutil.rmtree(version_dir, ignore_errors=True)
        raise


def _collect_images(profile: UserProfile) -> list[tuple[str | None, int]]:
    """
    Return a list of (commons_filename, desired_width) for all images in the profile.
    Filenames may be None (field not set); those are filtered in create_snapshot.
    """
    items: list[tuple[str | None, int]] = []

    items.append((profile.avatar_filename, _WIDTHS['avatar']))
    items.append((profile.biography_image_filename, _WIDTHS['biography']))

    for bs in _parse_json(profile.barnstars, []):
        items.append((bs.get('filename'), _WIDTHS['barnstar']))

    for po in _parse_json(profile.proud_of, []):
        items.append((po.get('filename'), _WIDTHS['proud_of']))

    return items


def _parse_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


# ── Coordinator entry point ───────────────────────────────────────────────────

def run_snapshot_job(username: str, app: Flask) -> None:
    """
    Coordinator entry point for admin-triggered snapshot jobs.
    Runs create_snapshot inside an app context with error handling.
    CardOverflowError is logged (not re-raised) so the coordinator continues.
    """
    with app.app_context():
        try:
            profile = UserProfile.query.filter_by(username=username).first()
            if profile is None:
                app.logger.warning('run_snapshot_job: no profile for %r', username)
                return
            create_snapshot(profile, triggered_by_user=False)
            app.logger.info('Snapshot created for %s', username)
        except CardOverflowError as exc:
            app.logger.warning(
                'Snapshot skipped for %s (card overflow): %s', username, exc
            )
        except Exception as exc:
            app.logger.error(
                'Snapshot failed for %s: %s', username, exc, exc_info=True
            )
            raise   # re-raise so _on_snapshot_done marks the queue row as error
        finally:
            db.session.remove()


# ── Inactive account pruning ──────────────────────────────────────────────────

def prune_inactive_user(profile: UserProfile) -> None:
    """
    Delete old snapshot versions for a user who has been inactive longer than
    SNAPSHOT_INACTIVE_PRUNE_DAYS.

    Keeps:
      - user_approved_snapshot_version (last user-triggered snapshot)
      - all versions created after user_approved_snapshot_version

    Deletes:
      - all versions strictly older than user_approved_snapshot_version

    No-op if:
      - The user has been active recently (updated_at within the threshold).
      - The user never explicitly finalized a card (user_approved_snapshot_version is NULL).
    """
    inactive_days = current_app.config['SNAPSHOT_INACTIVE_PRUNE_DAYS']
    inactive_since = utcnow() - timedelta(days=inactive_days)

    if profile.updated_at > inactive_since:
        return

    approved = profile.user_approved_snapshot_version
    if not approved:
        return   # never explicitly approved by user — do not prune

    snapshot_root = current_app.config['SNAPSHOT_ROOT']
    user_dir = assert_safe_path(
        os.path.join(snapshot_root, safe_username_dir(profile.username, profile.id))
    )

    if not os.path.isdir(user_dir):
        return

    versions = sorted(
        v for v in os.listdir(user_dir)
        if VERSION_RE.match(v)
        and not os.path.islink(os.path.join(user_dir, v))
    )

    for v in versions:
        if v < approved:
            target = assert_safe_path(os.path.join(user_dir, v))
            shutil.rmtree(target)
