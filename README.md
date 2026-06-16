# Gmail Scraper

Bulk-export a Gmail account (100k–500k messages) to a local SQLite database and
sharded `.eml` files. One-shot, resumable, idempotent. Runs as a Docker container.

## Prerequisites

1. **Google Cloud project** with the Gmail API enabled.
2. An **OAuth 2.0 Client ID** of type "Desktop app".
   - Console: *APIs & Services → Credentials → Create Credentials → OAuth client ID → Desktop app*
   - Download the JSON and save it as `./config/credentials.json`.
3. **Python 3.12+** on your host (only needed for the one-time auth bootstrap).
4. **Docker + Docker Compose**.

## Quick start

### 1. One-time auth bootstrap (host machine, not Docker)

```bash
pip install google-auth-oauthlib
python bootstrap_auth.py
```

A browser window opens. Sign in and grant read-only Gmail access. The script
writes `./config/token.json`. You only need to do this once — subsequent Docker
runs refresh the token automatically.

### 2. Build and run

```bash
docker compose build

# Enumerate all message IDs (Phase 1):
docker compose run --rm scraper python -m gmail_scraper enumerate

# Fetch and store all messages (Phase 2):
docker compose run --rm scraper python -m gmail_scraper fetch

# Or both in one shot:
docker compose run --rm scraper python -m gmail_scraper run
```

Check progress at any time:

```bash
docker compose run --rm scraper python -m gmail_scraper status
```

### Resuming after interruption

`docker stop` sends SIGTERM; the container finishes its in-flight batch, commits,
and exits cleanly. Re-running `fetch` skips all rows already marked `done`.

## Data layout

| Host path      | Contents                                  |
| -------------- | ----------------------------------------- |
| `./config/`    | `credentials.json`, `token.json`          |
| `./data/db/`   | `gmail.sqlite` — messages, labels, FTS5   |
| `./data/raw/`  | Sharded `.eml` files (`aa/bb/<id>.eml`)   |
| `./logs/`      | JSON-lines log files per run              |

## Environment variables

| Variable            | Default                    | Description                        |
| ------------------- | -------------------------- | ---------------------------------- |
| `CONFIG_DIR`        | `/config`                  | credentials.json / token.json      |
| `DB_PATH`           | `/data/db/gmail.sqlite`    | SQLite database path               |
| `RAW_DIR`           | `/data/raw`                | Root for sharded .eml files        |
| `LOG_DIR`           | `/logs`                    | Log output directory               |
| `INCLUDE_SPAM_TRASH`| `false`                    | Include spam/trash in enumeration  |
| `BATCH_SIZE`        | `100`                      | Gmail API batch size (max 100)     |
| `MAX_CONCURRENCY`   | `5`                        | Concurrent batch requests          |

CLI flags override env vars for any single invocation.

## Subcommands

```
auth        Verify token.json and confirm API access
enumerate   Phase 1: list all message IDs into fetch_queue + refresh labels
fetch       Phase 2: fetch, store .eml files, populate messages table
run         enumerate then fetch
status      Print queue counts (pending / done / error)
reparse     [stub] Re-parse .eml files from disk without re-hitting the API
```

## Backup

The entire dataset lives under `./data/`. Back it up with:

```bash
tar -czf gmail-backup-$(date +%Y%m%d).tar.gz data/
```

The `.eml` files are sufficient to re-parse everything; the SQLite DB is
derived from them. If the DB is lost, `reparse` (when implemented) will rebuild
it from disk.

## Development

```bash
pip install -e ".[dev]"
pytest tests/
```

Unit tests cover the parser, path-sharding, and backoff logic using only
fixture `.eml` files — no live API calls required.
