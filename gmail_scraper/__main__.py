"""CLI entry point: python -m gmail_scraper <subcommand>"""
import argparse
import os
import sys


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _bool_env(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, str(default)).lower()
    return val in ("1", "true", "yes")


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gmail_scraper",
        description="Gmail → SQLite bulk exporter",
    )
    parser.add_argument(
        "--config-dir",
        default=_env("CONFIG_DIR", "/config"),
        help="Directory containing credentials.json and token.json (default: $CONFIG_DIR)",
    )
    parser.add_argument(
        "--db-path",
        default=_env("DB_PATH", "/data/db/gmail.sqlite"),
        help="Path to SQLite database (default: $DB_PATH)",
    )
    parser.add_argument(
        "--raw-dir",
        default=_env("RAW_DIR", "/data/raw"),
        help="Directory for .eml files (default: $RAW_DIR)",
    )
    parser.add_argument(
        "--log-dir",
        default=_env("LOG_DIR", "/logs"),
        help="Directory for log files (default: $LOG_DIR)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # auth
    auth_p = sub.add_parser("auth", help="Verify token.json and confirm API access")

    # enumerate
    enum_p = sub.add_parser("enumerate", help="Phase 1: enumerate all message IDs")
    enum_p.add_argument(
        "--include-spam-trash",
        action="store_true",
        default=_bool_env("INCLUDE_SPAM_TRASH", False),
    )
    enum_p.add_argument(
        "--batch-size",
        type=int,
        default=_int_env("BATCH_SIZE", 500),
        help="IDs per DB insert batch (default: 500)",
    )

    # fetch
    fetch_p = sub.add_parser("fetch", help="Phase 2: fetch and store messages")
    fetch_p.add_argument(
        "--batch-size",
        type=int,
        default=_int_env("BATCH_SIZE", 100),
        help="Messages per Gmail batch request (default: 100, API max)",
    )
    fetch_p.add_argument(
        "--max-concurrency",
        type=int,
        default=_int_env("MAX_CONCURRENCY", 5),
        help="Concurrent batch requests (default: 5)",
    )
    fetch_p.add_argument(
        "--progress-every",
        type=int,
        default=500,
        help="Log a progress line every N messages (default: 500)",
    )

    # run
    run_p = sub.add_parser("run", help="Convenience: enumerate then fetch")
    run_p.add_argument("--include-spam-trash", action="store_true", default=_bool_env("INCLUDE_SPAM_TRASH", False))
    run_p.add_argument("--batch-size", type=int, default=_int_env("BATCH_SIZE", 100))
    run_p.add_argument("--max-concurrency", type=int, default=_int_env("MAX_CONCURRENCY", 5))

    # status
    sub.add_parser("status", help="Print queue counts and exit")

    # reparse (stub — not implemented yet)
    sub.add_parser("reparse", help="[stub] Re-parse .eml files without re-fetching")

    args = parser.parse_args()

    # Setup logging after we know the log dir
    from .log import setup_logging
    log = setup_logging(args.log_dir)

    if args.command == "auth":
        from .auth import verify_auth
        verify_auth(args.config_dir)

    elif args.command == "enumerate":
        from .enumerate import run_enumerate
        run_enumerate(
            config_dir=args.config_dir,
            db_path=args.db_path,
            include_spam_trash=args.include_spam_trash,
            batch_size=args.batch_size,
        )

    elif args.command == "fetch":
        from .fetch import run_fetch
        run_fetch(
            config_dir=args.config_dir,
            db_path=args.db_path,
            raw_dir=args.raw_dir,
            batch_size=args.batch_size,
            max_concurrency=args.max_concurrency,
            progress_every=args.progress_every,
        )

    elif args.command == "run":
        from .enumerate import run_enumerate
        from .fetch import run_fetch
        run_enumerate(
            config_dir=args.config_dir,
            db_path=args.db_path,
            include_spam_trash=args.include_spam_trash,
        )
        run_fetch(
            config_dir=args.config_dir,
            db_path=args.db_path,
            raw_dir=args.raw_dir,
            batch_size=args.batch_size,
            max_concurrency=args.max_concurrency,
        )

    elif args.command == "status":
        _cmd_status(args.db_path)

    elif args.command == "reparse":
        log.info("reparse is a stub — not yet implemented")
        print("reparse: not yet implemented.")
        sys.exit(0)


def _cmd_status(db_path: str) -> None:
    import sqlite3
    from pathlib import Path

    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT status, COUNT(*) as n FROM fetch_queue GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    total = sum(counts.values())

    msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    print(f"{'Status':<12} {'Count':>10}")
    print("-" * 24)
    for status in ("pending", "done", "error"):
        print(f"{status:<12} {counts.get(status, 0):>10,}")
    print("-" * 24)
    print(f"{'total':<12} {total:>10,}")
    print(f"\nMessages in DB : {msg_count:,}")

    conn.close()


if __name__ == "__main__":
    main()
