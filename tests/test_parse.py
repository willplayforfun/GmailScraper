"""Unit tests for gmail_scraper.parse."""
import json
from pathlib import Path

import pytest

from gmail_scraper.parse import parse_eml

FIXTURES = Path(__file__).parent / "fixtures"

_BASE_META = {
    "id": "msg001",
    "threadId": "thread001",
    "labelIds": ["INBOX", "UNREAD"],
    "snippet": "snippet text",
    "sizeEstimate": 1024,
    "historyId": "99",
    "internalDate": "1704067200000",  # 2024-01-01T00:00:00Z in ms
}


def _meta(**overrides):
    return {**_BASE_META, **overrides}


def test_plain_text_body():
    raw = (FIXTURES / "plain_text.eml").read_bytes()
    row = parse_eml(raw, _meta(id="msg001", labelIds=["INBOX"]))

    assert "plain text email body" in row["body_text"]
    assert row["body_html"] is None or row["body_html"] == ""
    assert row["from_addr"] == "alice@example.com"
    assert row["from_name"] == "Alice Example"
    assert row["subject"] == "Hello world"
    assert row["rfc822_message_id"] == "abc123@example.com"
    assert row["is_inbox"] == 1
    assert row["is_unread"] == 0


def test_multipart_html_and_mime_subject():
    raw = (FIXTURES / "multipart_html.eml").read_bytes()
    row = parse_eml(raw, _meta(id="msg002"))

    # MIME-encoded subject should be decoded
    assert "Café" in row["subject"]
    assert "plain text" in row["body_text"]
    assert "<html>" in row["body_html"]


def test_to_addrs_parsed_as_json_array():
    raw = (FIXTURES / "multipart_html.eml").read_bytes()
    row = parse_eml(raw, _meta(id="msg002"))

    to_list = json.loads(row["to_addrs"])
    assert isinstance(to_list, list)
    assert len(to_list) == 2
    # Addresses are lowercased
    assert any("r1@example.com" in a for a in to_list)
    assert any("r2@example.com" in a for a in to_list)

    cc_list = json.loads(row["cc_addrs"])
    assert any("cc@example.com" in a for a in cc_list)


def test_attachment_skipped():
    """Attachment parts must not appear in body_text."""
    raw = (FIXTURES / "with_attachment.eml").read_bytes()
    row = parse_eml(raw, _meta(id="msg003", labelIds=[]))

    assert "Please find the report" in row["body_text"]
    # PDF base64 content must not bleed into body
    assert "JVBERi0x" not in (row["body_text"] or "")
    assert "JVBERi0x" not in (row["body_html"] or "")


def test_label_flags():
    raw = (FIXTURES / "plain_text.eml").read_bytes()
    row = parse_eml(raw, _meta(id="msg001", labelIds=["INBOX", "UNREAD", "STARRED"]))

    assert row["is_inbox"] == 1
    assert row["is_unread"] == 1
    assert row["is_starred"] == 1
    assert set(row["label_ids"]) == {"INBOX", "UNREAD", "STARRED"}


def test_internal_date_cast():
    raw = (FIXTURES / "plain_text.eml").read_bytes()
    row = parse_eml(raw, _meta(id="msg001", internalDate="1704067200000"))
    assert row["internal_date"] == 1704067200000
    assert isinstance(row["internal_date"], int)


def test_history_id_cast():
    raw = (FIXTURES / "plain_text.eml").read_bytes()
    row = parse_eml(raw, _meta(id="msg001", historyId="12345"))
    assert row["history_id"] == 12345
    assert isinstance(row["history_id"], int)
