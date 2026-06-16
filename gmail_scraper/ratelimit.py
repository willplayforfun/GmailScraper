"""Exponential backoff helpers for Gmail API rate limits."""
import logging
import time
from typing import Callable, TypeVar

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

T = TypeVar("T")

_BACKOFF_START = 1.0
_BACKOFF_CAP = 60.0
_MAX_ATTEMPTS = 5


def is_retryable(exc: Exception) -> tuple[bool, float | None]:
    """Return (should_retry, retry_after_seconds_or_None)."""
    if isinstance(exc, HttpError):
        status = exc.resp.status
        retry_after = exc.resp.get("retry-after")
        if status in (429, 500, 502, 503, 504):
            return True, float(retry_after) if retry_after else None
    return False, None


def with_backoff(fn: Callable[[], T], label: str = "") -> T:
    """
    Call fn() with exponential backoff on retryable errors.
    Raises on the 5th consecutive failure.
    """
    delay = _BACKOFF_START
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:
            retryable, retry_after = is_retryable(exc)
            if not retryable or attempt == _MAX_ATTEMPTS:
                raise
            sleep_for = retry_after if retry_after is not None else min(delay, _BACKOFF_CAP)
            logger.warning(
                "Retryable error",
                extra={"label": label, "attempt": attempt, "sleep_s": sleep_for, "error": str(exc)},
            )
            time.sleep(sleep_for)
            delay = min(delay * 2, _BACKOFF_CAP)
    # unreachable
    raise RuntimeError("with_backoff: exhausted retries")
