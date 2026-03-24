"""
Tests for path-safety functions in src/snapshot.py.

assert_safe_path is the primary defence against path-traversal attacks in the
snapshot system. Every directory operation (create, delete, read) passes through
it before touching the filesystem.

Also covers _resolve_image_url, which calls the Commons MediaWiki API to obtain
a thumbnail URL. It must gracefully handle missing files and network errors.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from src.snapshot import assert_safe_path, _resolve_image_url


# ── assert_safe_path ──────────────────────────────────────────────────────────

class TestAssertSafePath:
    """
    assert_safe_path must raise ValueError for any path that resolves outside
    SNAPSHOT_ROOT, and return the resolved absolute path for valid paths.
    """

    @pytest.fixture()
    def snap_root(self, tmp_path, flask_app):
        """Configure a real temp directory as SNAPSHOT_ROOT and return it."""
        flask_app.config['SNAPSHOT_ROOT'] = str(tmp_path)
        return tmp_path

    def test_valid_child_path_returns_resolved_path(self, snap_root, flask_app):
        child = snap_root / 'user_1' / '20260501_120000_000001'
        child.mkdir(parents=True)

        with flask_app.app_context():
            result = assert_safe_path(str(child))

        assert result == str(child.resolve())

    def test_path_equal_to_root_raises(self, snap_root, flask_app):
        """
        The root directory itself is not a valid target — only proper children
        are permitted. This guards against operations like rmtree(SNAPSHOT_ROOT).
        """
        with flask_app.app_context():
            with pytest.raises(ValueError, match='escapes snapshot root'):
                assert_safe_path(str(snap_root))

    def test_dotdot_traversal_raises(self, snap_root, flask_app):
        """Classic ../.. traversal must be caught after path resolution."""
        traversal = str(snap_root / 'user_1' / '..' / '..' / 'etc' / 'passwd')

        with flask_app.app_context():
            with pytest.raises(ValueError, match='escapes snapshot root'):
                assert_safe_path(traversal)

    def test_sibling_directory_with_common_prefix_raises(self, snap_root, flask_app):
        """
        /data/snapshots-evil must not pass a check for /data/snapshots even
        though it has the root as a string prefix. The os.sep guard prevents this.
        """
        # Create a sibling that shares the root name as a prefix
        sibling = snap_root.parent / (snap_root.name + '-evil')
        sibling.mkdir()
        (sibling / 'payload').mkdir()

        with flask_app.app_context():
            with pytest.raises(ValueError, match='escapes snapshot root'):
                assert_safe_path(str(sibling / 'payload'))

    def test_absolute_path_outside_root_raises(self, snap_root, flask_app):
        with flask_app.app_context():
            with pytest.raises(ValueError):
                assert_safe_path('/etc/passwd')


# ── _resolve_image_url ────────────────────────────────────────────────────────

class TestResolveImageUrl:
    """
    _resolve_image_url calls the Commons MediaWiki API and returns a thumbnail
    URL. It must return None gracefully on missing files and network errors
    instead of raising.
    """

    def _make_api_response(self, *, missing: bool = False, thumb_url: str = '') -> MagicMock:
        """Build a mock requests.Response for the Commons imageinfo API."""
        page: dict = {}
        if missing:
            page['missing'] = ''
        elif thumb_url:
            page['imageinfo'] = [{'thumburl': thumb_url, 'url': thumb_url}]

        mock_resp = MagicMock()
        mock_resp.json.return_value = {'query': {'pages': {'-1': page}}}
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    def test_returns_thumbnail_url_on_success(self):
        expected_url = 'https://upload.wikimedia.org/thumb/Example.jpg/400px-Example.jpg'
        mock_resp = self._make_api_response(thumb_url=expected_url)

        with patch('src.snapshot.requests.get', return_value=mock_resp):
            result = _resolve_image_url('Example.jpg', width=400)

        assert result == expected_url

    def test_returns_none_for_missing_file(self):
        """A file marked 'missing' in the API response should yield None."""
        mock_resp = self._make_api_response(missing=True)

        with patch('src.snapshot.requests.get', return_value=mock_resp):
            result = _resolve_image_url('NonExistent.jpg', width=400)

        assert result is None

    def test_returns_none_on_network_error(self):
        """Network failures must be swallowed — missing images are handled gracefully."""
        with patch('src.snapshot.requests.get', side_effect=OSError('timeout')):
            result = _resolve_image_url('Example.jpg', width=400)

        assert result is None

    def test_returns_none_on_malformed_json(self):
        """An unexpected API response shape should not raise."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}          # no 'query' key
        mock_resp.raise_for_status.return_value = None

        with patch('src.snapshot.requests.get', return_value=mock_resp):
            result = _resolve_image_url('Example.jpg', width=400)

        assert result is None
