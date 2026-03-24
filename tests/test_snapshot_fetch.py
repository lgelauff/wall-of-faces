"""
Tests for src/snapshot.py — _fetch_image security constraints.

_fetch_image enforces:
  - https:// scheme only
  - upload.wikimedia.org host only
  - max 1 redirect hop
  - 20 MB response size cap
"""
import os
import pytest
from unittest.mock import MagicMock, patch, call


def _make_response(is_redirect=False, location=None, content_chunks=None, status_ok=True):
    """Build a mock requests response usable as a context manager."""
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__  = MagicMock(return_value=False)
    resp.is_redirect = is_redirect
    resp.headers = {"Location": location} if location else {}
    resp.raise_for_status = MagicMock()
    if not status_ok:
        from requests.exceptions import HTTPError
        resp.raise_for_status.side_effect = HTTPError("404")
    resp.iter_content = MagicMock(return_value=iter(content_chunks or [b"data"]))
    return resp


VALID_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Photo.jpg/400px-Photo.jpg"


class TestFetchImageScheme:
    def test_http_raises(self, tmp_path):
        from src.snapshot import _fetch_image
        with pytest.raises(ValueError, match="Only https allowed"):
            _fetch_image("http://upload.wikimedia.org/x.jpg", str(tmp_path / "out.jpg"))

    def test_ftp_raises(self, tmp_path):
        from src.snapshot import _fetch_image
        with pytest.raises(ValueError, match="Only https allowed"):
            _fetch_image("ftp://upload.wikimedia.org/x.jpg", str(tmp_path / "out.jpg"))

    def test_file_scheme_raises(self, tmp_path):
        from src.snapshot import _fetch_image
        with pytest.raises(ValueError, match="Only https allowed"):
            _fetch_image("file:///etc/passwd", str(tmp_path / "out.jpg"))


class TestFetchImageHost:
    def test_disallowed_host_raises(self, tmp_path):
        from src.snapshot import _fetch_image
        with pytest.raises(ValueError, match="Disallowed image host"):
            _fetch_image("https://evil.example.com/x.jpg", str(tmp_path / "out.jpg"))

    def test_wikipedia_org_rejected(self, tmp_path):
        from src.snapshot import _fetch_image
        with pytest.raises(ValueError, match="Disallowed image host"):
            _fetch_image("https://nl.wikipedia.org/x.jpg", str(tmp_path / "out.jpg"))

    def test_commons_wikimedia_org_rejected(self, tmp_path):
        # Only upload.wikimedia.org is allowed, not the main Commons domain
        from src.snapshot import _fetch_image
        with pytest.raises(ValueError, match="Disallowed image host"):
            _fetch_image("https://commons.wikimedia.org/x.jpg", str(tmp_path / "out.jpg"))


class TestFetchImageRedirect:
    def test_one_redirect_followed(self, tmp_path):
        dest = str(tmp_path / "out.jpg")
        redirect_resp = _make_response(is_redirect=True, location=VALID_URL)
        final_resp    = _make_response(content_chunks=[b"imagedata"])

        with patch("src.snapshot.requests.get", side_effect=[redirect_resp, final_resp]):
            from src.snapshot import _fetch_image
            _fetch_image(VALID_URL, dest)

        assert os.path.exists(dest)
        assert open(dest, "rb").read() == b"imagedata"

    def test_two_redirects_raises(self, tmp_path):
        dest = str(tmp_path / "out.jpg")
        from src.snapshot import _fetch_image
        # _depth=1 already means we've followed one; calling again exceeds limit
        with pytest.raises(ValueError, match="Too many redirects"):
            _fetch_image(VALID_URL, dest, _depth=2)

    def test_redirect_to_disallowed_host_raises(self, tmp_path):
        dest = str(tmp_path / "out.jpg")
        redirect_resp = _make_response(
            is_redirect=True,
            location="https://evil.example.com/ssrf.jpg"
        )
        with patch("src.snapshot.requests.get", return_value=redirect_resp):
            from src.snapshot import _fetch_image
            with pytest.raises(ValueError, match="Disallowed image host"):
                _fetch_image(VALID_URL, dest)

    def test_redirect_to_http_raises(self, tmp_path):
        dest = str(tmp_path / "out.jpg")
        redirect_resp = _make_response(
            is_redirect=True,
            location="http://upload.wikimedia.org/x.jpg"
        )
        with patch("src.snapshot.requests.get", return_value=redirect_resp):
            from src.snapshot import _fetch_image
            with pytest.raises(ValueError, match="Only https allowed"):
                _fetch_image(VALID_URL, dest)


class TestFetchImageSizeCap:
    def test_exactly_20mb_accepted(self, tmp_path):
        dest = str(tmp_path / "out.jpg")
        max_bytes = 20 * 1024 * 1024
        # Single chunk of exactly max_bytes
        resp = _make_response(content_chunks=[b"x" * max_bytes])
        with patch("src.snapshot.requests.get", return_value=resp):
            from src.snapshot import _fetch_image
            _fetch_image(VALID_URL, dest)  # must not raise
        assert os.path.getsize(dest) == max_bytes

    def test_one_byte_over_20mb_raises(self, tmp_path):
        dest = str(tmp_path / "out.jpg")
        max_bytes = 20 * 1024 * 1024
        # Two chunks: first fills exactly max_bytes, second adds 1 byte
        resp = _make_response(content_chunks=[b"x" * max_bytes, b"y"])
        with patch("src.snapshot.requests.get", return_value=resp):
            from src.snapshot import _fetch_image
            with pytest.raises(ValueError, match="20 MB"):
                _fetch_image(VALID_URL, dest)

    def test_small_image_written_correctly(self, tmp_path):
        dest = str(tmp_path / "out.jpg")
        data = b"JPEG" + b"\x00" * 100
        resp = _make_response(content_chunks=[data])
        with patch("src.snapshot.requests.get", return_value=resp):
            from src.snapshot import _fetch_image
            _fetch_image(VALID_URL, dest)
        assert open(dest, "rb").read() == data

    def test_chunked_write_accumulates_correctly(self, tmp_path):
        dest = str(tmp_path / "out.jpg")
        chunks = [b"abc", b"def", b"ghi"]
        resp = _make_response(content_chunks=chunks)
        with patch("src.snapshot.requests.get", return_value=resp):
            from src.snapshot import _fetch_image
            _fetch_image(VALID_URL, dest)
        assert open(dest, "rb").read() == b"abcdefghi"
