"""
Phase 2: fetch pending messages from Gmail, write .eml files, populate DB.

Concurrency model: a ThreadPoolExecutor submits batches of up to BATCH_SIZE
requests. Each worker calls BatchHttpRequest.execute() (one HTTPS round trip
per batch). MAX_CONCURRENCY controls how many batches fly at once.
"""
import base64
import logging
import os
import signal
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .auth import load_credentials
from .db import open_db
from .parse import parse_eml
from .ratelimit import is_retryable, with_backoff
from .storage import write_eml

logger = logging.getLogger(__name__)

_SHUTDOWN = False


def _handle_signal(signum, frame):
    global _SHUTDOWN
    logger.info("Shutdown signal received — finishing in-flight batch then exiting")
    _SHUTDOWN = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _fetch_pending_ids(conn: sqlite3.Connection, limit: int = 5000) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM fetch_queue WHERE status = 'pending' AND attempts < 5 LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["id"] for r in rows]


def _mark_done(conn: sqlite3.Connection, msg_id: str) -> None:
    conn.execute(
        "UPDATE fetch_queue SET status='done', updated_at=? WHERE id=?",
        (int(time.time()), msg_id),
    )


def _mark_error(conn: sqlite3.Connection, msg_id: str, error: str, attempts: int) -> None:
    new_status = "error" if attempts >= 5 else "pending"
    conn.execute(
        "UPDATE fetch_queue SET status=?, error=?, attempts=?, updated_at=? WHERE id=?",
        (new_status, error[:2000], attempts, int(time.time()), msg_id),
    )


def _insert_message(conn: sqlite3.Connection, row: dict[str, Any], raw_path: str) -> None:
    label_ids: list[str] = row.pop("label_ids", [])
    row["raw_path"] = raw_path

    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" * len(row))
    conn.execute(
        f"INSERT OR REPLACE INTO messages({cols}) VALUES ({placeholders})",
        list(row.values()),
    )
    if label_ids:
        conn.executemany(
            "INSERT OR IGNORE INTO message_labels(message_id, label_id) VALUES (?, ?)",
            [(row["id"], lid) for lid in label_ids],
        )


def _process_batch(
    service,
    conn: sqlite3.Connection,
    raw_dir: str,
    msg_ids: list[str],
) -> tuple[int, int]:
    """
    Issue a BatchHttpRequest for msg_ids, write .eml files, insert rows.
    Returns (success_count, error_count).
    """
    results: dict[str, dict | Exception] = {}

    def make_callback(mid: str):
        def callback(request_id, response, exception):
            if exception:
                results[mid] = exception
            else:
                results[mid] = response
        return callback

    batch = service.new_batch_http_request()
    for mid in msg_ids:
        batch.add(
            service.users().messages().get(userId="me", id=mid, format="raw"),
            callback=make_callback(mid),
        )

    with_backoff(lambda b=batch: b.execute(), label=f"batch({len(msg_ids)})")

    success = 0
    errors = 0

    # Read current attempts counts in one query
    placeholders = ",".join("?" * len(msg_ids))
    attempts_map = {
        r["id"]: r["attempts"]
        for r in conn.execute(
            f"SELECT id, attempts FROM fetch_queue WHERE id IN ({placeholders})",
            msg_ids,
        ).fetchall()
    }

    for mid in msg_ids:
        result = results.get(mid)
        attempts = attempts_map.get(mid, 0) + 1

        if isinstance(result, Exception):
            retryable, _ = is_retryable(result)
            logger.warning(
                "Message fetch error",
                extra={"msg_id": mid, "attempt": attempts, "error": str(result)},
            )
            _mark_error(conn, mid, str(result), attempts)
            errors += 1
            continue

        if result is None:
            _mark_error(conn, mid, "No response in batch", attempts)
            errors += 1
            continue

        try:
            raw_b64 = result.get("raw", "")
            raw_bytes = base64.urlsafe_b64decode(raw_b64 + "==")
            rel_path = write_eml(raw_dir, mid, raw_bytes)
            row = parse_eml(raw_bytes, result)
            _insert_message(conn, row, rel_path)
            _mark_done(conn, mid)
            success += 1
        except Exception as exc:
            logger.exception("Parse/store error", extra={"msg_id": mid})
            _mark_error(conn, mid, str(exc), attempts)
            errors += 1

    conn.commit()
    return success, errors


def run_fetch(
    config_dir: str,
    db_path: str,
    raw_dir: str,
    batch_size: int = 100,
    max_concurrency: int = 5,
    progress_every: int = 500,
) -> None:
    creds = load_credentials(config_dir)

    conn = open_db(db_path)
    os.makedirs(raw_dir, exist_ok=True)

    total_done = conn.execute(
        "SELECT COUNT(*) FROM fetch_queue WHERE status='done'"
    ).fetchone()[0]
    total_success = 0
    total_errors = 0
    start_time = time.time()
    last_progress = total_done

    logger.info("Fetch starting", extra={"already_done": total_done})

    while not _SHUTDOWN:
        pending_ids = _fetch_pending_ids(conn, limit=batch_size * max_concurrency * 2)
        if not pending_ids:
            break

        # Split pending into batches of batch_size
        batches = [
            pending_ids[i : i + batch_size]
            for i in range(0, len(pending_ids), batch_size)
        ]

        # Limit in-flight batches to max_concurrency
        with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
            # Build a fresh service per thread (googleapiclient is not thread-safe)
            futures = {
                pool.submit(
                    _process_batch,
                    build("gmail", "v1", credentials=creds, cache_discovery=False),
                    conn,
                    raw_dir,
                    chunk,
                ): chunk
                for chunk in batches
            }
            for future in as_completed(futures):
                if _SHUTDOWN:
                    break
                try:
                    s, e = future.result()
                    total_success += s
                    total_errors += e
                    total_done += s
                except Exception:
                    logger.exception("Batch future raised")

                if total_done - last_progress >= progress_every:
                    elapsed = time.time() - start_time
                    rate = total_success / elapsed if elapsed > 0 else 0
                    pending_count = conn.execute(
                        "SELECT COUNT(*) FROM fetch_queue WHERE status='pending'"
                    ).fetchone()[0]
                    eta = pending_count / rate if rate > 0 else float("inf")
                    logger.info(
                        "Progress",
                        extra={
                            "done": total_done,
                            "rate_per_sec": round(rate, 1),
                            "pending": pending_count,
                            "eta_sec": round(eta),
                            "errors": total_errors,
                        },
                    )
                    last_progress = total_done

    # Final status
    row = conn.execute(
        "SELECT status, COUNT(*) as n FROM fetch_queue GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["n"] for r in row}
    logger.info("Fetch complete", extra={"counts": counts})
    print(
        f"Fetch complete: {total_success} fetched, {total_errors} errors this run. "
        f"Queue: {counts}"
    )
