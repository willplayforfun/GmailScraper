"""Unit tests for gmail_scraper.ratelimit."""
import pytest
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError

from gmail_scraper.ratelimit import is_retryable, with_backoff


def _make_http_error(status: int, retry_after: str | None = None) -> HttpError:
    resp = MagicMock()
    resp.status = status
    resp.get = lambda k, default=None: retry_after if k == "retry-after" else default
    return HttpError(resp=resp, content=b"error")


def test_429_is_retryable():
    exc = _make_http_error(429)
    retryable, retry_after = is_retryable(exc)
    assert retryable is True
    assert retry_after is None


def test_retry_after_honored():
    exc = _make_http_error(429, retry_after="5")
    retryable, retry_after = is_retryable(exc)
    assert retryable is True
    assert retry_after == 5.0


def test_404_not_retryable():
    exc = _make_http_error(404)
    retryable, _ = is_retryable(exc)
    assert retryable is False


def test_non_http_error_not_retryable():
    retryable, _ = is_retryable(ValueError("bad"))
    assert retryable is False


def test_with_backoff_succeeds_on_first_try():
    fn = MagicMock(return_value=42)
    result = with_backoff(fn)
    assert result == 42
    fn.assert_called_once()


def test_with_backoff_retries_then_succeeds():
    exc = _make_http_error(429)
    fn = MagicMock(side_effect=[exc, exc, 99])
    with patch("gmail_scraper.ratelimit.time.sleep"):
        result = with_backoff(fn)
    assert result == 99
    assert fn.call_count == 3


def test_with_backoff_raises_after_max_attempts():
    exc = _make_http_error(500)
    fn = MagicMock(side_effect=exc)
    with patch("gmail_scraper.ratelimit.time.sleep"):
        with pytest.raises(HttpError):
            with_backoff(fn)
    assert fn.call_count == 5
