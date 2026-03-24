"""
Tests for snapshot.py internals: _collect_images and prune_inactive_user.

_collect_images
    Extracts (filename, width) tuples from a UserProfile for all image types:
    avatar, biography image, barnstars (JSON list), and proud_of (JSON list).
    Malformed JSON in barnstars/proud_of must fall back to an empty list
    rather than crashing create_snapshot.

prune_inactive_user
    Deletes old snapshot directories for accounts that have been inactive
    longer than SNAPSHOT_INACTIVE_PRUNE_DAYS. The function must:
      - Be a no-op when the user is still active.
      - Be a no-op when the user never explicitly finalized a card.
      - Delete versions *older than* user_approved_snapshot_version.
      - Keep the approved version and everything after it.
      - Use lexicographic comparison, which is correct for the
        YYYYMMDD_HHMMSS_ffffff format.
"""

import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.db import UserProfile, db as _db
from src.snapshot import _collect_images, prune_inactive_user
from src.utils import utcnow
from conftest import TEST_USERNAME


# ── _collect_images ───────────────────────────────────────────────────────────

class TestCollectImages:
    """
    _collect_images must handle all four image sources and survive malformed
    JSON without raising — create_snapshot relies on it being safe.
    """

    def _make_profile(self, **kwargs):
        """Return a SimpleNamespace that mimics a UserProfile."""
        defaults = dict(
            avatar_filename=None,
            biography_image_filename=None,
            barnstars=None,
            proud_of=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_all_none_fields_return_two_entries(self):
        """
        Even with all fields None, avatar and biography produce (None, width)
        placeholders so callers can rely on stable list length.
        """
        profile = self._make_profile()
        items = _collect_images(profile)

        # At minimum: avatar + biography_image
        assert len(items) >= 2

    def test_avatar_filename_included(self):
        profile = self._make_profile(avatar_filename='Avatar.jpg')
        items = _collect_images(profile)

        filenames = [fn for fn, _ in items]
        assert 'Avatar.jpg' in filenames

    def test_biography_image_included(self):
        profile = self._make_profile(biography_image_filename='Bio.jpg')
        items = _collect_images(profile)

        filenames = [fn for fn, _ in items]
        assert 'Bio.jpg' in filenames

    def test_barnstar_filenames_included(self):
        import json
        barnstars = json.dumps([
            {'filename': 'Barnstar1.png', 'label': 'Original Barnstar'},
            {'filename': 'Barnstar2.png', 'label': 'Civility Barnstar'},
        ])
        profile = self._make_profile(barnstars=barnstars)
        items = _collect_images(profile)

        filenames = [fn for fn, _ in items]
        assert 'Barnstar1.png' in filenames
        assert 'Barnstar2.png' in filenames

    def test_proud_of_filenames_included(self):
        import json
        proud_of = json.dumps([
            {'filename': 'DYK.svg', 'label': 'Did You Know'},
        ])
        profile = self._make_profile(proud_of=proud_of)
        items = _collect_images(profile)

        filenames = [fn for fn, _ in items]
        assert 'DYK.svg' in filenames

    def test_malformed_barnstars_json_does_not_raise(self):
        """Corrupted barnstars JSON must fall back to an empty list."""
        profile = self._make_profile(barnstars='not-valid-json{{{')
        # Must not raise
        items = _collect_images(profile)

        # Only the two baseline entries (avatar, biography) should be present
        assert len(items) == 2

    def test_malformed_proud_of_json_does_not_raise(self):
        """Corrupted proud_of JSON must fall back to an empty list."""
        profile = self._make_profile(proud_of='[broken')
        items = _collect_images(profile)

        assert len(items) == 2


# ── prune_inactive_user ───────────────────────────────────────────────────────

class TestPruneInactiveUser:
    """
    prune_inactive_user removes old snapshot directories for inactive users.

    The function must be conservative: it only prunes when both conditions
    hold: the user is inactive AND they have an approved version. The
    lexicographic sort order of YYYYMMDD_HHMMSS_ffffff is intentional.
    """

    def _make_snapshot_dirs(self, user_dir, versions):
        """Create empty snapshot version directories under user_dir."""
        for v in versions:
            os.makedirs(os.path.join(user_dir, v), exist_ok=True)

    @pytest.fixture()
    def snap_root(self, tmp_path, flask_app):
        flask_app.config['SNAPSHOT_ROOT'] = str(tmp_path)
        flask_app.config['SNAPSHOT_INACTIVE_PRUNE_DAYS'] = 30
        return tmp_path

    def _get_profile(self, flask_app):
        return UserProfile.query.filter_by(username=TEST_USERNAME).first()

    def test_active_user_not_pruned(self, snap_root, flask_app, profile):
        """
        A user who was active very recently must not have any directories removed,
        regardless of how many snapshot versions exist.
        """
        from src.snapshot import safe_username_dir

        with flask_app.app_context():
            profile = self._get_profile(flask_app)
            # Mark as recently active
            profile.updated_at = utcnow()
            profile.user_approved_snapshot_version = '20260101_000000_000001'
            _db.session.commit()

            user_dir = os.path.join(
                str(snap_root),
                safe_username_dir(profile.username, profile.id),
            )
            os.makedirs(user_dir, exist_ok=True)
            self._make_snapshot_dirs(user_dir, [
                '20260101_000000_000001',
                '20260102_000000_000002',
            ])

            prune_inactive_user(profile)

        remaining = [
            e for e in os.listdir(user_dir)
            if os.path.isdir(os.path.join(user_dir, e))
        ]
        assert len(remaining) == 2

    def test_no_approved_version_not_pruned(self, snap_root, flask_app, profile):
        """
        A user who never explicitly finalized (user_approved_snapshot_version
        is NULL) must not be pruned — we cannot determine a safe cutoff point.
        """
        from src.snapshot import safe_username_dir

        with flask_app.app_context():
            profile = self._get_profile(flask_app)
            # Mark as inactive but no approved version
            profile.updated_at = utcnow() - timedelta(days=60)
            profile.user_approved_snapshot_version = None
            _db.session.commit()

            user_dir = os.path.join(
                str(snap_root),
                safe_username_dir(profile.username, profile.id),
            )
            os.makedirs(user_dir, exist_ok=True)
            self._make_snapshot_dirs(user_dir, [
                '20260101_000000_000001',
                '20260102_000000_000002',
            ])

            prune_inactive_user(profile)

        remaining = [
            e for e in os.listdir(user_dir)
            if os.path.isdir(os.path.join(user_dir, e))
        ]
        assert len(remaining) == 2

    def test_old_versions_deleted_approved_kept(self, snap_root, flask_app, profile):
        """
        For an inactive user with an approved version, all versions strictly
        older than approved must be removed; approved itself must survive.
        """
        from src.snapshot import safe_username_dir

        approved = '20260301_000000_000003'

        with flask_app.app_context():
            profile = self._get_profile(flask_app)
            profile.updated_at = utcnow() - timedelta(days=60)
            profile.user_approved_snapshot_version = approved
            _db.session.commit()

            user_dir = os.path.join(
                str(snap_root),
                safe_username_dir(profile.username, profile.id),
            )
            os.makedirs(user_dir, exist_ok=True)
            self._make_snapshot_dirs(user_dir, [
                '20260101_000000_000001',   # older — should be deleted
                '20260201_000000_000002',   # older — should be deleted
                approved,                   # the approved version — must survive
                '20260401_000000_000004',   # newer  — must survive
            ])

            prune_inactive_user(profile)

        remaining = sorted(
            e for e in os.listdir(user_dir)
            if os.path.isdir(os.path.join(user_dir, e))
        )
        assert '20260101_000000_000001' not in remaining
        assert '20260201_000000_000002' not in remaining
        assert approved in remaining
        assert '20260401_000000_000004' in remaining

    def test_nonexistent_user_dir_is_noop(self, snap_root, flask_app, profile):
        """
        If no snapshot directory exists for the user yet, prune must be a
        silent no-op — not raise FileNotFoundError.
        """
        with flask_app.app_context():
            profile = self._get_profile(flask_app)
            profile.updated_at = utcnow() - timedelta(days=60)
            profile.user_approved_snapshot_version = '20260101_000000_000001'
            _db.session.commit()

            # No directory created — must not raise
            prune_inactive_user(profile)
