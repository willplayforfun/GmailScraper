# Gmail-to-SQLite Scraper — Implementation Spec

## Purpose

Build a Dockerized Python tool that performs a one-shot bulk export of a Gmail
account (100k–500k messages expected) into a local SQLite database plus on-disk
`.eml` files. The output is the seed dataset for downstream work: link
scraping, corpus building, ML categorization of incoming mail, etc.

This spec is the source of truth for the initial implementation. Anything not
specified here is the implementer's call, but the implementer should flag
non-obvious decisions in code comments. Anything that warrants a serious decision should be escalated to the user overseeing the implementing agent.

## Scope

**In scope (this implementation):**

- OAuth 2.0 setup against the Gmail API.
- Two-phase fetch: enumerate all message IDs, then fetch each message.
- Persist raw RFC822 bytes as `.eml` files on disk (sharded directory layout).
- Parse and persist headers, decoded bodies, and label associations into SQLite.
- Resume cleanly after interruption.
- Be idempotent: re-running against an existing DB skips already-fetched messages.
- Run as a Docker container with host-mounted directories for config, raw eml,
  and the SQLite database.

**Out of scope (explicitly deferred):**

- Attachments. Skip them entirely. Do not download or store attachment payloads.
- Incremental sync via `users.history.list`. The schema must support it later
  (store `history_id` and a `sync_state` row for the max seen), but the
  incremental code path is not built in this iteration.
- Link extraction from bodies, web scraping of linked content, full-text search
  population beyond the FTS5 table definition, ML/categorization.
- A UI. CLI only.

## Runtime: Docker

The tool runs as a Docker container. The implementer should produce a
`Dockerfile` and a `docker-compose.yml`.

**Mount points (host → container):**

| Host path (example) | Container path | Purpose                                               |
| ------------------- | -------------- | ----------------------------------------------------- |
| `./config`          | `/config`      | OAuth `credentials.json`, saved `token.json`, `.env`. |
| `./data/raw`        | `/data/raw`    | Sharded `.eml` files.                                 |
| `./data/db`         | `/data/db`     | The SQLite database file.                             |
| `./logs`            | `/logs`        | Run logs.                                             |

The container must NOT bake credentials in. `credentials.json` is mounted in;
`token.json` is written on first run via an OAuth flow and persisted to the
mounted config volume so subsequent runs reuse it.

**OAuth flow consideration:** the initial consent flow requires a browser
redirect. The implementer should use the `InstalledAppFlow` "console" /
out-of-band flow, OR document a one-time host-side bootstrap step (run a small
script on the host to produce `token.json`, then drop it in `./config`). Ask the user to choose one while implementing - present pros and cons.

**Compose example the implementer should produce:**

```yaml
services:
  scraper:
    build: .
    volumes:
      - ./config:/config
      - ./data/raw:/data/raw
      - ./data/db:/data/db
      - ./logs:/logs
    environment:
      - CONFIG_DIR=/config
      - RAW_DIR=/data/raw
      - DB_PATH=/data/db/gmail.sqlite
      - LOG_DIR=/logs
      - INCLUDE_SPAM_TRASH=false
      - BATCH_SIZE=100
      - MAX_CONCURRENCY=5
```

The image should be based on `python:3.12-slim` or similar. No system deps
beyond what `google-api-python-client` needs (none, basically).

## CLI

Single entry point with subcommands. Suggested invocation inside the container:

```
python -m gmail_scraper <subcommand> [options]
```

Subcommands:

- `auth` — runs the OAuth flow, writes `token.json` to `$CONFIG_DIR`. Exits.
- `enumerate` — Phase 1. Lists all message IDs and writes them to `fetch_queue`.
  Also refreshes the `labels` table. Safe to re-run; uses `INSERT OR IGNORE`.
- `fetch` — Phase 2. Pulls pending IDs from `fetch_queue` and fetches them.
  Resumable. Safe to re-run.
- `run` — convenience: runs `enumerate` then `fetch`.
- `status` — prints counts (total queued, pending, done, error) and exits.

All subcommands respect env vars listed in the compose example above. CLI flags
may override env vars; env vars override defaults.

## Gmail API details

- **Scopes:** `https://www.googleapis.com/auth/gmail.readonly`. Nothing more.
- **Library:** `google-api-python-client`, `google-auth`, `google-auth-oauthlib`.
- **Quota:** 250 quota units/user/second, 1B/day. `messages.get` costs 5 units.
  `messages.list` costs 5 units. Targeting ~40 messages/sec sustained is safe.
- **Batching:** use `BatchHttpRequest` with batches of 100 (the documented max).
- **Concurrency:** the implementer may run multiple batch requests in parallel
  via threads (`MAX_CONCURRENCY` env var). Start with 5 and tune. Do not exceed
  the per-second quota.
- **`includeSpamTrash`:** controlled by env var, default false.
- **Format for fetches:** `format='raw'`. This returns the full RFC822 message
  as base64url. We decode, write to disk, and parse with Python's `email` module
  using `policy=email.policy.default`.
- **Snippet / labelIds / threadId / internalDate / historyId / sizeEstimate:**
  these are NOT in the raw payload. The implementer must either:
  - call `messages.get(format='raw')` and accept that the response *does* still
    include `id`, `threadId`, `labelIds`, `snippet`, `sizeEstimate`,
    `historyId`, and `internalDate` as top-level fields alongside `raw` — verify
    this against current API behavior; OR
  - call `messages.get(format='metadata')` separately. Prefer the first if the
    API still returns metadata alongside raw, since it's one round trip.

## Rate limiting and retries

- Exponential backoff on HTTP 429 and 5xx responses. Start at 1s, double on each
  retry, cap at 60s, max 5 retries before marking the queue row as `error`.
- Honor `Retry-After` if present.
- The token has a ~1hr access lifetime. The `google-auth` `Credentials` object
  refreshes automatically when constructed with a refresh token. Verify the
  refresh token is persisted in `token.json`.
- Log each retry with the message ID and the error.

## Schema

SQLite. Apply this exact schema (modulo bug fixes the implementer notices —
flag them in a comment).

```sql
CREATE TABLE sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- Keys used: 'last_history_id', 'last_full_sync_at', 'schema_version'.

CREATE TABLE labels (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL              -- 'system' or 'user'
);

CREATE TABLE messages (
    id                TEXT PRIMARY KEY,   -- Gmail message id; dedup key
    thread_id         TEXT NOT NULL,
    rfc822_message_id TEXT,               -- from Message-ID: header
    history_id        INTEGER,
    internal_date     INTEGER,            -- ms since epoch (Gmail-provided)
    date_header       TEXT,               -- raw Date: header
    from_addr         TEXT,
    from_name         TEXT,
    to_addrs          TEXT,               -- JSON array of strings
    cc_addrs          TEXT,               -- JSON array
    bcc_addrs         TEXT,               -- JSON array
    subject           TEXT,
    snippet           TEXT,
    body_text         TEXT,               -- decoded text/plain part(s)
    body_html         TEXT,               -- decoded text/html part(s)
    size_estimate     INTEGER,
    is_unread         INTEGER NOT NULL DEFAULT 0,
    is_starred        INTEGER NOT NULL DEFAULT 0,
    is_inbox          INTEGER NOT NULL DEFAULT 0,
    raw_path          TEXT NOT NULL,      -- relative path under $RAW_DIR
    fetched_at        INTEGER NOT NULL,   -- unix seconds
    parse_version     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_messages_thread  ON messages(thread_id);
CREATE INDEX idx_messages_date    ON messages(internal_date);
CREATE INDEX idx_messages_from    ON messages(from_addr);
CREATE INDEX idx_messages_history ON messages(history_id);

CREATE TABLE message_labels (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    label_id   TEXT NOT NULL REFERENCES labels(id),
    PRIMARY KEY (message_id, label_id)
);
CREATE INDEX idx_message_labels_label ON message_labels(label_id);

CREATE TABLE fetch_queue (
    id         TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | done | error
    error      TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER
);
CREATE INDEX idx_fetch_queue_status ON fetch_queue(status);

CREATE VIRTUAL TABLE messages_fts USING fts5(
    subject, body_text, from_addr, from_name,
    content='messages',
    content_rowid='rowid'
);
-- FTS triggers (insert/update/delete) to keep messages_fts in sync.
-- Implementer: add the standard FTS5 external-content triggers.
```

**Schema version:** insert `('schema_version', '1')` into `sync_state` on init.
The implementer should add a tiny migration runner so v2 is straightforward.

**SQLite pragmas to set on every connection:**

```
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA temp_store   = MEMORY;
```

## Raw `.eml` storage

- Decode the `raw` field (base64url) to bytes.
- Path: `$RAW_DIR/<aa>/<bb>/<full_message_id>.eml` where `aa`/`bb` are the first
  two pairs of hex chars from a SHA-1 of the message ID (or just the first 4
  chars of the ID itself — pick one, document it). This keeps any single
  directory under ~thousands of files.
- Store the path in `messages.raw_path` as a relative path (from `$RAW_DIR`),
  not absolute, so the dataset is portable.
- Write atomically: write to `<path>.tmp` then rename.

## Parsing rules

Implementer parses each `.eml` after writing. Use stdlib `email` with
`policy=email.policy.default`.

- `from_addr` / `from_name`: parse `From:` header with `email.utils.parseaddr`.
  Lowercase the address.
- `to_addrs` / `cc_addrs` / `bcc_addrs`: `getaddresses()` on the respective
  headers, store as JSON array of `"name <addr>"` strings OR just addresses —
  pick one, be consistent. Lowercase addresses.
- `subject`: decode MIME-encoded words.
- `rfc822_message_id`: from `Message-ID:` header, strip angle brackets.
- `date_header`: raw `Date:` header string, unparsed.
- `internal_date`: from the API response, not the header. Authoritative.
- `body_text`: concatenate all `text/plain` parts, decoded with their declared
  charset (fall back to utf-8 with `errors='replace'`).
- `body_html`: same but for `text/html`.
- If a message is single-part, that part is the body.
- **Do not extract attachments.** Skip any part whose `Content-Disposition` is
  `attachment` or which has a filename. (Inline images are also skipped.)

`is_unread`, `is_starred`, `is_inbox`: derived from presence of `UNREAD`,
`STARRED`, `INBOX` in the message's `labelIds`. Insert the booleans AND the
full label set into `message_labels`.

If parsing fails, mark the queue row `error` with the exception text. The
`.eml` is still on disk so it can be re-parsed later by bumping `parse_version`.

## Re-parse path (future-proofing, not built now)

The schema supports re-parsing every message from disk later without re-hitting
the API. The implementer should leave a stub command `reparse` that's wired up
but does nothing yet (or implement it if cheap — it's just "for each message
where parse_version < N, re-read raw_path, re-parse, update row").

## Logging

- Structured logs (JSON lines) to `$LOG_DIR/scraper-<UTC-timestamp>.log` and
  also to stdout.
- Per-batch summary: batch size, success count, error count, elapsed.
- Progress line every N messages (default 500): total done, rate (msg/sec),
  ETA based on `fetch_queue` pending count.
- On retry: log message ID, attempt number, error.

## Failure handling

- Any exception during fetch of an individual message: increment `attempts`,
  store error text, leave status `pending` if attempts < 5, else `error`.
- Any exception during a whole batch (e.g., auth failure): log, sleep with
  backoff, retry the batch. Don't advance.
- DB transactions: commit after each batch. Do not run the whole fetch in one
  transaction.
- Graceful shutdown: trap SIGTERM/SIGINT, finish the in-flight batch, commit,
  exit 0. This makes `docker stop` safe.

## Acceptance criteria

The implementation is done when all of the following hold:

1. `docker compose run --rm scraper python -m gmail_scraper auth` produces a
   `token.json` in the mounted config dir.
2. `docker compose run --rm scraper python -m gmail_scraper enumerate` populates
   `fetch_queue` with every message ID from the account and `labels` with all
   labels.
3. `docker compose run --rm scraper python -m gmail_scraper fetch` processes
   pending queue rows, writes `.eml` files, populates `messages` and
   `message_labels`, and converges to zero pending rows (modulo a small number
   of `error` rows for genuinely malformed messages).
4. Killing the container mid-fetch (`docker stop`) and re-running `fetch`
   resumes without re-downloading any `done` row. Verify by row count.
5. Re-running `enumerate` against an already-populated DB is a no-op for
   already-known IDs (no duplicates, no errors).
6. The `messages` table has expected columns populated for a hand-checked
   sample of 10 emails covering: a plain text mail, a multipart HTML mail, a
   mail with non-ASCII subject, a mail with multiple recipients, an unread
   inbox mail, a starred mail, and a mail in a user-created label.
7. The `messages_fts` table returns hits for `MATCH 'some-known-word'`.
8. `python -m gmail_scraper status` prints sane counts.
9. The README documents: prerequisites (Google Cloud project + OAuth client),
   the auth bootstrap, the run command, where data ends up, and how to back up
   (just tar the `data/` dir).

## Suggested project layout

```
gmail-scraper/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml          # or requirements.txt
├── README.md
├── gmail_scraper/
│   ├── __init__.py
│   ├── __main__.py         # CLI dispatch
│   ├── auth.py             # OAuth flow
│   ├── db.py               # schema init, connection helpers, pragmas
│   ├── enumerate.py        # Phase 1
│   ├── fetch.py            # Phase 2
│   ├── parse.py            # .eml → row dict
│   ├── storage.py          # .eml path sharding, atomic write
│   ├── ratelimit.py        # backoff helpers
│   └── log.py
└── tests/
    ├── fixtures/           # a handful of saved .eml files
    ├── test_parse.py
    └── test_storage.py
```

Unit tests should cover at minimum: the parser (using fixture `.eml` files),
the path-sharding function, and the backoff helper. Integration tests against
the live API are not required.

## Dependencies

```
google-api-python-client
google-auth
google-auth-oauthlib
google-auth-httplib2
```

Stdlib for everything else (`sqlite3`, `email`, `json`, `argparse`, `logging`).

## Notes for the implementer

- The Gmail API's batch response order matches request order, but use the
  per-request callback pattern rather than positional indexing — clearer code.
- `messages.list` with no `q` returns ALL mail (including All Mail / Archive),
  not just Inbox. That's what we want. Don't filter.
- `internalDate` is a string in the JSON response but represents milliseconds.
  Cast to int.
- Be paranoid about character encodings in bodies. Some old mail has lying
  `charset` declarations. `errors='replace'` is the pragmatic call.
- If the implementer hits an API behavior that contradicts this spec, they
  should follow the API and note the discrepancy in a code comment plus the
  README. Don't fight the API.
