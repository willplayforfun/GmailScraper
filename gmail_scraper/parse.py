"""Parse a raw RFC822 .eml into a dict ready for DB insertion."""
import email
import email.policy
import email.utils
import json
import logging
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _decode_payload(part: email.message.Message) -> str:
    """Decode a MIME part payload to a string, tolerating bad charsets."""
    charset = part.get_content_charset() or "utf-8"
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    return payload.decode(charset, errors="replace")


def _is_attachment(part: email.message.Message) -> bool:
    disp = part.get_content_disposition() or ""
    if disp.lower() == "attachment":
        return True
    if part.get_filename():
        return True
    return False


def _collect_bodies(msg: email.message.Message) -> tuple[str, str]:
    """Walk all parts and collect text/plain and text/html bodies."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if _is_attachment(part):
                continue
            ct = part.get_content_type()
            if ct == "text/plain":
                plain_parts.append(_decode_payload(part))
            elif ct == "text/html":
                html_parts.append(_decode_payload(part))
    else:
        ct = msg.get_content_type()
        if ct == "text/plain":
            plain_parts.append(_decode_payload(msg))
        elif ct == "text/html":
            html_parts.append(_decode_payload(msg))

    return "\n".join(plain_parts), "\n".join(html_parts)


def _parse_addr_list(header_value: str | None) -> str:
    """Parse a header containing a list of addresses → JSON array of 'name <addr>' strings."""
    if not header_value:
        return "[]"
    pairs = email.utils.getaddresses([header_value])
    result = []
    for name, addr in pairs:
        addr_lower = addr.lower()
        if name:
            result.append(f"{name} <{addr_lower}>")
        else:
            result.append(addr_lower)
    return json.dumps(result)


def parse_eml(raw_bytes: bytes, api_meta: dict[str, Any]) -> dict[str, Any]:
    """
    Parse raw RFC822 bytes into a row dict for the messages table.

    api_meta must contain the top-level fields returned alongside 'raw' by
    messages.get(format='raw'): id, threadId, labelIds, snippet,
    sizeEstimate, historyId, internalDate.

    API note: messages.get(format='raw') does return these metadata fields
    alongside the raw payload in the current API (verified against behavior
    described in the Gmail API reference). If Google ever stops including them
    here, a separate metadata call would be needed.
    """
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    from_name, from_addr = email.utils.parseaddr(msg.get("From", ""))
    from_addr = from_addr.lower()

    body_text, body_html = _collect_bodies(msg)

    raw_msg_id = msg.get("Message-ID", "")
    rfc822_message_id = raw_msg_id.strip().strip("<>")

    label_ids: list[str] = api_meta.get("labelIds") or []

    return {
        "id": api_meta["id"],
        "thread_id": api_meta["threadId"],
        "rfc822_message_id": rfc822_message_id or None,
        "history_id": int(api_meta["historyId"]) if api_meta.get("historyId") else None,
        "internal_date": int(api_meta["internalDate"]) if api_meta.get("internalDate") else None,
        "date_header": msg.get("Date"),
        "from_addr": from_addr or None,
        "from_name": from_name or None,
        "to_addrs": _parse_addr_list(msg.get("To")),
        "cc_addrs": _parse_addr_list(msg.get("Cc")),
        "bcc_addrs": _parse_addr_list(msg.get("Bcc")),
        "subject": str(msg.get("Subject", "") or ""),
        "snippet": api_meta.get("snippet"),
        "body_text": body_text or None,
        "body_html": body_html or None,
        "size_estimate": api_meta.get("sizeEstimate"),
        "is_unread": int("UNREAD" in label_ids),
        "is_starred": int("STARRED" in label_ids),
        "is_inbox": int("INBOX" in label_ids),
        "fetched_at": int(time.time()),
        "parse_version": 1,
        "label_ids": label_ids,
    }
