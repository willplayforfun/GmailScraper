"""Sharded .eml file storage with atomic writes."""
import os
import sys
from pathlib import Path


def eml_path(raw_dir: str, message_id: str) -> Path:
    """
    Return the Path where the .eml for message_id should be stored.

    Sharding strategy: use the first 4 characters of the Gmail message ID
    directly (they are already hex-like alphanum) to form two shard levels:
      <raw_dir>/<aa>/<bb>/<message_id>.eml
    where aa = chars 0-1, bb = chars 2-3.

    This keeps any single directory under ~65k entries even with 500k messages
    (500k / 256 shards ≈ 2000 per leaf). Portable because we use the ID itself,
    not a hash, so no extra dependency.
    """
    aa = message_id[:2]
    bb = message_id[2:4]
    return Path(raw_dir) / aa / bb / f"{message_id}.eml"


def eml_rel_path(message_id: str) -> str:
    """Return the relative path (from $RAW_DIR) for portability."""
    aa = message_id[:2]
    bb = message_id[2:4]
    return f"{aa}/{bb}/{message_id}.eml"


def write_eml(raw_dir: str, message_id: str, raw_bytes: bytes) -> str:
    """
    Atomically write raw_bytes to the sharded .eml path.
    Returns the relative path stored in messages.raw_path.
    """
    path = eml_path(raw_dir, message_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".eml.tmp")
    tmp.write_bytes(raw_bytes)
    # os.replace is atomic on POSIX and replaces-in-place on Windows
    # (Path.rename raises FileExistsError on Windows when target exists)
    os.replace(tmp, path)
    return eml_rel_path(message_id)
