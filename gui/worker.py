"""
Worker thread that runs gmail_scraper phases and posts typed events to a queue.

Event shapes posted to the queue:
  {"type": "log",    "record": LogRecord}
  {"type": "done",   "success": int, "errors": int}
  {"type": "error",  "exc": str}         ← unhandled exception in the worker
"""
import logging
import queue
import threading
import traceback

from .log_handler import QueueHandler
from .settings import Settings


class Worker:
    def __init__(self, q: queue.Queue, settings: Settings, mode: str = "run") -> None:
        """
        mode: "enumerate" | "fetch" | "run"
        """
        self.q = q
        self.settings = settings
        self.mode = mode

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._handler = QueueHandler(q)

    # ── public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()

        # Reset the module-level shutdown flag before starting
        import gmail_scraper.fetch as _fetch
        _fetch._SHUTDOWN = False

        logging.getLogger().addHandler(self._handler)
        self._thread = threading.Thread(target=self._run, daemon=True, name="gmail-worker")
        self._thread.start()

    def stop(self) -> None:
        """Signal the worker to finish its current batch and exit."""
        self._stop_event.set()
        import gmail_scraper.fetch as _fetch
        _fetch._SHUTDOWN = True

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── internal ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            s = self.settings
            s.config_dir().mkdir(parents=True, exist_ok=True)
            s.db_path().parent.mkdir(parents=True, exist_ok=True)
            s.raw_dir().mkdir(parents=True, exist_ok=True)
            s.log_dir().mkdir(parents=True, exist_ok=True)

            from gmail_scraper.enumerate import run_enumerate
            from gmail_scraper.fetch import run_fetch

            if self.mode in ("enumerate", "run"):
                run_enumerate(
                    config_dir=str(s.config_dir()),
                    db_path=str(s.db_path()),
                    include_spam_trash=s.include_spam_trash,
                )

            if not self._stop_event.is_set() and self.mode in ("fetch", "run"):
                run_fetch(
                    config_dir=str(s.config_dir()),
                    db_path=str(s.db_path()),
                    raw_dir=str(s.raw_dir()),
                    batch_size=s.batch_size,
                    max_concurrency=s.max_concurrency,
                )

            self.q.put({"type": "done", "success": 0, "errors": 0})

        except Exception:
            self.q.put({"type": "error", "exc": traceback.format_exc()})
        finally:
            logging.getLogger().removeHandler(self._handler)
