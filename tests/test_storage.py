"""Unit tests for gmail_scraper.storage."""
import tempfile
from pathlib import Path

import pytest

from gmail_scraper.storage import eml_path, eml_rel_path, write_eml


def test_eml_rel_path_sharding():
    rel = eml_rel_path("AbCdEfGhIj")
    assert rel == "Ab/Cd/AbCdEfGhIj.eml"


def test_eml_path_structure():
    path = eml_path("/data/raw", "AbCdEfGhIj")
    assert path == Path("/data/raw/Ab/Cd/AbCdEfGhIj.eml")


def test_write_eml_creates_file():
    with tempfile.TemporaryDirectory() as tmp:
        rel = write_eml(tmp, "AbCdEfGhIj", b"raw email content")
        full = Path(tmp) / rel
        assert full.exists()
        assert full.read_bytes() == b"raw email content"


def test_write_eml_is_atomic(monkeypatch):
    """Verify .tmp file is used and replaced atomically (no partial writes visible)."""
    replaced_pairs = []
    import os as _os
    original_replace = _os.replace

    def track_replace(src, dst):
        replaced_pairs.append((str(src), str(dst)))
        return original_replace(src, dst)

    monkeypatch.setattr("gmail_scraper.storage.os.replace", track_replace)

    with tempfile.TemporaryDirectory() as tmp:
        write_eml(tmp, "XxYyZz1234", b"data")
        assert len(replaced_pairs) == 1
        src, dst = replaced_pairs[0]
        assert src.endswith(".eml.tmp")
        assert dst.endswith("XxYyZz1234.eml")


def test_write_eml_idempotent():
    """Re-writing the same message ID overwrites cleanly."""
    with tempfile.TemporaryDirectory() as tmp:
        write_eml(tmp, "Aa00123456", b"first")
        write_eml(tmp, "Aa00123456", b"second")
        path = eml_path(tmp, "Aa00123456")
        assert path.read_bytes() == b"second"
