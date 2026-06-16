"""Persistent settings stored next to the executable."""
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def exe_dir() -> Path:
    """
    Directory containing the executable (frozen) or the repo root (source).
    Everything portable — settings, data — lives relative to this path.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # Running from source: two levels up from gui/settings.py → repo root
    return Path(__file__).parent.parent


@dataclass
class Settings:
    data_dir: str | None = None          # None → default (exe_dir/data)
    batch_size: int = 100
    max_concurrency: int = 5
    include_spam_trash: bool = False
    email: str = ""                      # cached after first successful auth

    # ── derived paths ─────────────────────────────────────────────────────────

    def data_root(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir)
        return exe_dir() / "data"

    def config_dir(self) -> Path:
        return self.data_root() / "config"

    def db_path(self) -> Path:
        return self.data_root() / "db" / "gmail.sqlite"

    def raw_dir(self) -> Path:
        return self.data_root() / "raw"

    def log_dir(self) -> Path:
        return self.data_root() / "logs"

    # ── persistence ───────────────────────────────────────────────────────────

    @classmethod
    def settings_path(cls) -> Path:
        return exe_dir() / "GmailScraper.settings.json"

    @classmethod
    def load(cls) -> "Settings":
        path = cls.settings_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {f for f in cls.__dataclass_fields__}
                return cls(**{k: v for k, v in data.items() if k in known})
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        self.settings_path().write_text(
            json.dumps(asdict(self), indent=2), encoding="utf-8"
        )
