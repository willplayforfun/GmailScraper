"""Structured JSON logging to file + stdout."""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        extra = {
            k: v
            for k, v in record.__dict__.items()
            if k not in logging.LogRecord.__dict__ and not k.startswith("_")
            and k not in ("msg", "args", "levelname", "levelno", "pathname",
                          "filename", "module", "exc_info", "exc_text",
                          "stack_info", "lineno", "funcName", "created",
                          "msecs", "relativeCreated", "thread", "threadName",
                          "processName", "process", "name", "message",
                          "taskName")
        }
        if extra:
            obj.update(extra)
        return json.dumps(obj, default=str)


def setup_logging(log_dir: str | None = None) -> logging.Logger:
    log_dir = log_dir or os.environ.get("LOG_DIR", "/logs")
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = Path(log_dir) / f"scraper-{stamp}.log"

    formatter = JsonFormatter()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stdout_handler)

    return logging.getLogger("gmail_scraper")
