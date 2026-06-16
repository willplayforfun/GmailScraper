"""Phase 1: enumerate all Gmail message IDs into fetch_queue, refresh labels."""
import logging
import sqlite3
import time
from typing import Iterator

from googleapiclient.discovery import build

from .auth import load_credentials
from .db import open_db
from .ratelimit import with_backoff

logger = logging.getLogger(__name__)


def _list_pages(
    service, include_spam_trash: bool, start_page_token: str | None = None
) -> Iterator[tuple[list[str], str | None]]:
    """Yield (ids_on_page, next_page_token) for each page of messages.list."""
    kwargs: dict = {"userId": "me", "includeSpamTrash": include_spam_trash,
                    "maxResults": 500}
    page_token = start_page_token

    while True:
        if page_token:
            kwargs["pageToken"] = page_token

        response = with_backoff(
            lambda kw=kwargs: service.users().messages().list(**kw).execute(),
            label="messages.list",
        )

        ids = [m["id"] for m in response.get("messages", [])]
        next_token = response.get("nextPageToken")
        yield ids, next_token
        if not next_token:
            break
        page_token = next_token


def _load_resume_token(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM sync_state WHERE key = 'enum_page_token'"
    ).fetchone()
    return row["value"] if row else None


def _save_resume_token(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute(
            "INSERT OR REPLACE INTO sync_state(key, value) VALUES ('enum_page_token', ?)",
            (token,),
        )
    else:
        conn.execute("DELETE FROM sync_state WHERE key = 'enum_page_token'")
    conn.commit()


def _refresh_labels(service, conn: sqlite3.Connection) -> None:
    response = with_backoff(
        lambda: service.users().labels().list(userId="me").execute(),
        label="labels.list",
    )
    labels = response.get("labels", [])
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO labels(id, name, type) VALUES (?, ?, ?)",
            [(lbl["id"], lbl["name"], lbl["type"].lower()) for lbl in labels],
        )
    logger.info("Labels refreshed", extra={"count": len(labels)})


def run_enumerate(
    config_dir: str,
    db_path: str,
    include_spam_trash: bool = False,
    batch_size: int = 500,
    stop_event=None,
) -> None:
    creds = load_credentials(config_dir)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    conn = open_db(db_path)

    profile = with_backoff(
        lambda: service.users().getProfile(userId="me").execute(),
        label="getProfile",
    )
    total_estimate = profile.get("messagesTotal", 0)

    resume_token = _load_resume_token(conn)
    if resume_token:
        logger.info("Resuming enumeration from saved page token.")
    else:
        logger.info("Starting enumeration", extra={"total_estimate": total_estimate})

    _refresh_labels(service, conn)

    inserted = 0
    skipped = 0
    enumerated = 0
    now = int(time.time())

    def flush(ids: list[str]) -> None:
        nonlocal inserted, skipped
        cur = conn.executemany(
            "INSERT OR IGNORE INTO fetch_queue(id, status, updated_at) VALUES (?, 'pending', ?)",
            [(mid, now) for mid in ids],
        )
        conn.commit()
        inserted += cur.rowcount
        skipped += len(ids) - cur.rowcount

    try:
        for page_ids, next_token in _list_pages(service, include_spam_trash, resume_token):
            if stop_event and stop_event.is_set():
                logger.info("Enumeration stopped by user.")
                break
            flush(page_ids)
            enumerated += len(page_ids)
            _save_resume_token(conn, next_token)
            logger.info("Queued batch", extra={
                "inserted": inserted, "skipped": skipped,
                "enumerated": enumerated, "total_estimate": total_estimate,
            })
    except Exception as exc:
        if resume_token and "invalid" in str(exc).lower():
            logger.warning("Saved page token expired — restarting enumeration from the beginning.")
            _save_resume_token(conn, None)
            run_enumerate(config_dir, db_path, include_spam_trash, batch_size, stop_event)
            return
        raise

    if not (stop_event and stop_event.is_set()):
        _save_resume_token(conn, None)
        logger.info(
            "Enumerate done",
            extra={"inserted": inserted, "skipped_existing": skipped},
        )
        print(f"Enumerate complete: {inserted} new IDs queued, {skipped} already known.")
