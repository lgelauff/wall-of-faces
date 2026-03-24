"""
Tests for src/replicas.py — registration date parsing and edit count logic.

The DB engine is mocked via patch on _centralauth so no MySQL connection
is needed.  The data-parsing logic in get_global_user_info is the main
focus (bytes/str/datetime handling for gu_registration).
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from src.replicas import ReplicaUnavailableError


# ── Helper: build a mock row ──────────────────────────────────────────────────

def _row(gu_registration, gu_editcount):
    row = MagicMock()
    row.gu_registration = gu_registration
    row.gu_editcount    = gu_editcount
    return row


def _wiki_row(lu_wiki, lu_editcount):
    row = MagicMock()
    row.lu_wiki      = lu_wiki
    row.lu_editcount = lu_editcount
    return row


_UNSET = object()  # sentinel — distinguishes "not provided" from None


def _mock_engine(fetchone_return=_UNSET, fetchall_return=_UNSET):
    """Return a mock engine whose .connect() context manager returns a mock conn."""
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__  = MagicMock(return_value=False)
    if fetchone_return is not _UNSET:
        conn.execute.return_value.fetchone.return_value = fetchone_return
    if fetchall_return is not _UNSET:
        conn.execute.return_value.fetchall.return_value = fetchall_return
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine


# ── ReplicaUnavailableError ───────────────────────────────────────────────────

class TestReplicaUnavailableError:
    def test_is_exception(self):
        err = ReplicaUnavailableError("test")
        assert isinstance(err, Exception)

    def test_message_preserved(self):
        err = ReplicaUnavailableError("no file")
        assert "no file" in str(err)


# ── get_global_user_info ──────────────────────────────────────────────────────

class TestGetGlobalUserInfo:
    def _call(self, row):
        engine = _mock_engine(fetchone_return=row)
        with patch("src.replicas._centralauth", return_value=engine):
            from src.replicas import get_global_user_info
            return get_global_user_info("TestUser")

    def test_user_not_found_returns_none(self):
        assert self._call(None) is None

    def test_string_registration_date_parsed(self):
        row = _row("20120314120000", 12847)
        result = self._call(row)
        assert result["registration_date"] == datetime(2012, 3, 14, 12, 0, 0, tzinfo=timezone.utc)

    def test_bytes_registration_date_parsed(self):
        row = _row(b"20120314120000", 500)
        result = self._call(row)
        assert result["registration_date"] == datetime(2012, 3, 14, 12, 0, 0, tzinfo=timezone.utc)

    def test_bytearray_registration_date_parsed(self):
        row = _row(bytearray(b"20120314120000"), 500)
        result = self._call(row)
        assert result["registration_date"] == datetime(2012, 3, 14, 12, 0, 0, tzinfo=timezone.utc)

    def test_datetime_registration_date_passed_through(self):
        dt = datetime(2010, 6, 1, 0, 0, 0)  # naive
        row = _row(dt, 100)
        result = self._call(row)
        assert result["registration_date"] == datetime(2010, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_aware_datetime_preserved(self):
        dt = datetime(2010, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        row = _row(dt, 100)
        result = self._call(row)
        assert result["registration_date"].tzinfo == timezone.utc

    def test_invalid_date_string_gives_none(self):
        row = _row("not-a-date", 100)
        result = self._call(row)
        assert result["registration_date"] is None

    def test_empty_string_registration_gives_none(self):
        row = _row("", 100)
        result = self._call(row)
        assert result["registration_date"] is None

    def test_none_registration_gives_none(self):
        row = _row(None, 100)
        result = self._call(row)
        assert result["registration_date"] is None

    def test_edit_count_always_none(self):
        # gu_editcount is not available on Toolforge replicas; always returns None
        row = _row("20120314120000", 42)
        result = self._call(row)
        assert result["global_edit_count"] is None

    def test_returns_dict_with_expected_keys(self):
        row = _row("20120314120000", 100)
        result = self._call(row)
        assert set(result.keys()) == {"registration_date", "global_edit_count"}

    def test_date_string_truncated_to_14_chars(self):
        # MediaWiki timestamps can have extra chars; only first 14 used
        row = _row("20120314120000999extra", 1)
        result = self._call(row)
        assert result["registration_date"] == datetime(2012, 3, 14, 12, 0, 0, tzinfo=timezone.utc)


# ── get_wiki_edit_counts ──────────────────────────────────────────────────────

def _wiki_list_row(lu_wiki: str):
    row = MagicMock()
    row.lu_wiki = lu_wiki
    return row


def _xtools_response(count):
    """Return a mock requests.Response for an XTools simple_editcount call."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"total_edit_count": count}
    resp.raise_for_status.return_value = None
    return resp


class TestGetWikiEditCounts:
    def _call(self, wiki_list, counts_by_wiki: dict):
        """
        wiki_list: list of lu_wiki strings returned by centralauth
        counts_by_wiki: dict of wiki → total_edit_count (or None to simulate 404/missing)
        """
        ca_engine = _mock_engine(fetchall_return=[_wiki_list_row(w) for w in wiki_list])

        def xtools_get(url, **kwargs):
            # Extract wiki from URL: .../simple_editcount/{wiki}/{username}
            parts = url.rstrip("/").split("/")
            wiki = parts[-2]
            count = counts_by_wiki.get(wiki)
            if count is None:
                resp = MagicMock()
                resp.status_code = 404
                return resp
            return _xtools_response(count)

        with patch("src.replicas._centralauth", return_value=ca_engine), \
             patch("src.replicas.requests.get", side_effect=xtools_get):
            from src.replicas import get_wiki_edit_counts
            return get_wiki_edit_counts("TestUser")

    def test_empty_wiki_list_returns_empty_dict(self):
        assert self._call([], {}) == {}

    def test_maps_wiki_to_count(self):
        result = self._call(["nlwiki", "commonswiki"], {"nlwiki": 12847, "commonswiki": 543})
        assert result == {"nlwiki": 12847, "commonswiki": 543}

    def test_counts_coerced_to_int(self):
        result = self._call(["nlwiki"], {"nlwiki": 500})
        assert isinstance(result["nlwiki"], int)

    def test_wiki_with_no_user_row_excluded(self):
        result = self._call(["nlwiki", "commonswiki"], {"nlwiki": 100})
        assert "commonswiki" not in result
        assert result["nlwiki"] == 100

    def test_multiple_wikis(self):
        result = self._call(
            ["nlwiki", "wikidatawiki", "commonswiki"],
            {"nlwiki": 10000, "wikidatawiki": 250, "commonswiki": 88},
        )
        assert len(result) == 3
        assert result["wikidatawiki"] == 250

    def test_zero_count_excluded(self):
        # wikis with 0 edits are not included
        result = self._call(["nlwiki"], {"nlwiki": 0})
        assert "nlwiki" not in result


# ── _load_replica_credentials ─────────────────────────────────────────────────

class TestLoadReplicaCredentials:
    def test_raises_when_file_missing(self):
        with patch("os.path.exists", return_value=False):
            from src.replicas import _load_replica_credentials
            with pytest.raises(ReplicaUnavailableError, match="not found"):
                _load_replica_credentials()

    def test_raises_when_key_missing(self, tmp_path):
        cfg_file = tmp_path / "replica.my.cnf"
        cfg_file.write_text("[client]\nuser=foo\n")  # password missing
        with patch("os.path.expanduser", return_value=str(cfg_file)):
            from src.replicas import _load_replica_credentials
            with pytest.raises(ReplicaUnavailableError, match="missing expected key"):
                _load_replica_credentials()

    def test_returns_user_password(self, tmp_path):
        cfg_file = tmp_path / "replica.my.cnf"
        cfg_file.write_text("[client]\nuser=testuser\npassword=s3cr3t\n")
        with patch("os.path.expanduser", return_value=str(cfg_file)):
            from src.replicas import _load_replica_credentials
            user, password = _load_replica_credentials()
        assert user == "testuser"
        assert password == "s3cr3t"
