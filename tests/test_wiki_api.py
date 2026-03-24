"""
Tests for wiki._api retry logic.

_api makes MediaWiki API GET requests and retries on transient network errors.
The docstring says "not on 4xx", but the implementation catches all
RequestException, which includes HTTPError (raised by raise_for_status on 4xx).
This test file documents the actual behaviour so that any future change to the
retry policy is visible and intentional.

We also verify the happy-path response parsing and that the retry limit is
respected (no infinite loop).
"""

from unittest.mock import MagicMock, call, patch

import pytest
import requests

from src.wiki import _api


# ── Happy path ────────────────────────────────────────────────────────────────

class TestApiHappyPath:

    def test_returns_parsed_json(self):
        """A successful response must return the parsed dict."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {'batchcomplete': True, 'query': {}}

        with patch('src.wiki._session') as mock_session:
            mock_session.get.return_value = mock_resp
            result = _api('https://nl.wikipedia.org', {'action': 'query'})

        assert result == {'batchcomplete': True, 'query': {}}

    def test_injects_format_defaults(self):
        """format=json and formatversion=2 must be added when absent."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {}

        with patch('src.wiki._session') as mock_session:
            mock_session.get.return_value = mock_resp
            _api('https://nl.wikipedia.org', {'action': 'query'})

        _, kwargs = mock_session.get.call_args
        params = kwargs.get('params', mock_session.get.call_args[0][1] if len(mock_session.get.call_args[0]) > 1 else {})
        # Check the params dict that was actually passed to session.get
        call_params = mock_session.get.call_args.kwargs.get(
            'params', mock_session.get.call_args.args[1] if len(mock_session.get.call_args.args) > 1 else {}
        )
        assert call_params.get('format') == 'json'
        assert call_params.get('formatversion') == '2'

    def test_calls_correct_api_url(self):
        """The request must hit /w/api.php on the given wiki URL."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {}

        with patch('src.wiki._session') as mock_session:
            mock_session.get.return_value = mock_resp
            _api('https://nl.wikipedia.org', {})

        url_called = mock_session.get.call_args.args[0]
        assert url_called == 'https://nl.wikipedia.org/w/api.php'


# ── Retry behaviour ───────────────────────────────────────────────────────────

class TestApiRetry:
    """
    _api retries up to `retries` times (default 2) on RequestException.
    After exhausting retries, the last exception must propagate.
    """

    def test_retries_on_connection_error(self):
        """
        A transient ConnectionError should trigger retries. With retries=1,
        the function must call the session exactly twice before re-raising.
        """
        with patch('src.wiki._session') as mock_session:
            with patch('src.wiki.time.sleep'):   # suppress actual sleep in tests
                mock_session.get.side_effect = requests.ConnectionError('timeout')

                with pytest.raises(requests.ConnectionError):
                    _api('https://nl.wikipedia.org', {}, retries=1)

                assert mock_session.get.call_count == 2   # initial + 1 retry

    def test_reraises_after_all_retries_exhausted(self):
        """After retries=2 the raised exception must be the original one."""
        with patch('src.wiki._session') as mock_session:
            with patch('src.wiki.time.sleep'):
                mock_session.get.side_effect = requests.Timeout('took too long')

                with pytest.raises(requests.Timeout):
                    _api('https://nl.wikipedia.org', {}, retries=2)

                assert mock_session.get.call_count == 3   # initial + 2 retries

    def test_succeeds_on_second_attempt(self):
        """
        If the first call raises but the second succeeds, the result must be
        returned without propagating the transient error.
        """
        good_resp = MagicMock()
        good_resp.raise_for_status.return_value = None
        good_resp.json.return_value = {'ok': True}

        with patch('src.wiki._session') as mock_session:
            with patch('src.wiki.time.sleep'):
                mock_session.get.side_effect = [
                    requests.ConnectionError('blip'),
                    good_resp,
                ]
                result = _api('https://nl.wikipedia.org', {}, retries=1)

        assert result == {'ok': True}
        assert mock_session.get.call_count == 2

    def test_http_error_is_retried(self):
        """
        HTTPError (raised by raise_for_status on 4xx/5xx) is a RequestException.
        The current implementation retries it — this test documents that behaviour.

        NOTE: Retrying 4xx errors (e.g. 404, 403) wastes time since those responses
        are deterministic. This is a known limitation of the current implementation.
        See the QA report (M8) for context.
        """
        bad_resp = MagicMock()
        bad_resp.raise_for_status.side_effect = requests.HTTPError('404')

        with patch('src.wiki._session') as mock_session:
            with patch('src.wiki.time.sleep'):
                mock_session.get.return_value = bad_resp

                with pytest.raises(requests.HTTPError):
                    _api('https://nl.wikipedia.org', {}, retries=1)

                # HTTPError is retried (current behaviour — not necessarily ideal)
                assert mock_session.get.call_count == 2

    def test_no_retry_when_retries_zero(self):
        """With retries=0, a single failure must raise immediately."""
        with patch('src.wiki._session') as mock_session:
            with patch('src.wiki.time.sleep'):
                mock_session.get.side_effect = requests.ConnectionError('fail')

                with pytest.raises(requests.ConnectionError):
                    _api('https://nl.wikipedia.org', {}, retries=0)

                assert mock_session.get.call_count == 1
