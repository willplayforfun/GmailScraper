"""SQLite schema, connection helpers, and migration runner."""
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS labels (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                TEXT PRIMARY KEY,
    thread_id         TEXT NOT NULL,
    rfc822_message_id TEXT,
    history_id        INTEGER,
    internal_date     INTEGER,
    date_header       TEXT,
    from_addr         TEXT,
    from_name         TEXT,
    to_addrs          TEXT,
    cc_addrs          TEXT,
    bcc_addrs         TEXT,
    subject           TEXT,
    snippet           TEXT,
    body_text         TEXT,
    body_html         TEXT,
    size_estimate     INTEGER,
    is_unread         INTEGER NOT NULL DEFAULT 0,
    is_starred        INTEGER NOT NULL DEFAULT 0,
    is_inbox          INTEGER NOT NULL DEFAULT 0,
    raw_path          TEXT NOT NULL,
    fetched_at        INTEGER NOT NULL,
    parse_version     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_messages_thread  ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_date    ON messages(internal_date);
CREATE INDEX IF NOT EXISTS idx_messages_from    ON messages(from_addr);
CREATE INDEX IF NOT EXISTS idx_messages_history ON messages(history_id);

CREATE TABLE IF NOT EXISTS message_labels (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    label_id   TEXT NOT NULL REFERENCES labels(id),
    PRIMARY KEY (message_id, label_id)
);
CREATE INDEX IF NOT EXISTS idx_message_labels_label ON message_labels(label_id);

CREATE TABLE IF NOT EXISTS fetch_queue (
    id         TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'pending',
    error      TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fetch_queue_status ON fetch_queue(status);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    subject, body_text, from_addr, from_name,
    content='messages',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert
AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, subject, body_text, from_addr, from_name)
    VALUES (new.rowid, new.subject, new.body_text, new.from_addr, new.from_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete
AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, subject, body_text, from_addr, from_name)
    VALUES ('delete', old.rowid, old.subject, old.body_text, old.from_addr, old.from_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update
AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, subject, body_text, from_addr, from_name)
    VALUES ('delete', old.rowid, old.subject, old.body_text, old.from_addr, old.from_name);
    INSERT INTO messages_fts(rowid, subject, body_text, from_addr, from_name)
    VALUES (new.rowid, new.subject, new.body_text, new.from_addr, new.from_name);
END;
"""

# Future migrations go here: {from_version: sql_string}
_MIGRATIONS: dict[int, str] = {
    # example: 1: "ALTER TABLE messages ADD COLUMN new_col TEXT;"
}


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous  = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store   = MEMORY")


def open_db(db_path: str) -> sqlite3.Connection:
    """Open (and init/migrate) the SQLite database. Returns a connection."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    _init_schema(conn)
    _run_migrations(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.execute(
        "INSERT OR IGNORE INTO sync_state(key, value) VALUES ('schema_version', ?)",
        (str(CURRENT_SCHEMA_VERSION),),
    )
    conn.commit()


def _run_migrations(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT value FROM sync_state WHERE key = 'schema_version'"
    ).fetchone()
    version = int(row["value"]) if row else 0

    for from_ver, sql in sorted(_MIGRATIONS.items()):
        if version == from_ver:
            logger.info("Applying migration", extra={"from_version": from_ver})
            conn.executescript(sql)
            new_ver = from_ver + 1
            conn.execute(
                "UPDATE sync_state SET value = ? WHERE key = 'schema_version'",
                (str(new_ver),),
            )
            conn.commit()
            version = new_ver
