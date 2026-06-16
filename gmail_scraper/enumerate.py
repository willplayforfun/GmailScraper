"""Phase 1: enumerate all Gmail message IDs into fetch_queue, refresh labels."""
import logging
import os
import sqlite3
import time
from typing import Iterator

from googleapiclient.discovery import build

from .auth import load_credentials
from .db import open_db
from .ratelimit import with_backoff

logger = logging.getLogger(__name__)


def _list_all_message_ids(service, include_spam_trash: bool) -> Iterator[str]:
    """Yield every message ID in the account via paginated messages.list."""
    kwargs: dict = {"userId": "me", "includeSpamTrash": include_spam_trash}
    page_token = None
    total = 0

    while True:
        if page_token:
            kwargs["pageToken"] = page_token

        response = with_backoff(
            lambda kw=kwargs: service.users().messages().list(**kw).execute(),
            label="messages.list",
        )

        messages = response.get("messages", [])
        for m in messages:
            yield m["id"]
        total += len(messages)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    logger.info("Enumeration complete", extra={"total_ids": total})


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
) -> None:
    creds = load_credentials(config_dir)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    conn = open_db(db_path)

    _refresh_labels(service, conn)

    inserted = 0
    skipped = 0
    buf: list[str] = []
    now = int(time.time())

    def flush(buf: list[str]) -> None:
        nonlocal inserted, skipped
        cur = conn.executemany(
            "INSERT OR IGNORE INTO fetch_queue(id, status, updated_at) VALUES (?, 'pending', ?)",
            [(mid, now) for mid in buf],
        )
        conn.commit()
        inserted += cur.rowcount
        skipped += len(buf) - cur.rowcount

    for msg_id in _list_all_message_ids(service, include_spam_trash):
        buf.append(msg_id)
        if len(buf) >= batch_size:
            flush(buf)
            buf.clear()
            logger.info("Queued batch", extra={"inserted": inserted, "skipped": skipped})

    if buf:
        flush(buf)

    logger.info(
        "Enumerate done",
        extra={"inserted": inserted, "skipped_existing": skipped},
    )
    print(f"Enumerate complete: {inserted} new IDs queued, {skipped} already known.")
