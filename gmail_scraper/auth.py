"""
Auth helpers used inside the container.

The initial token.json is produced by bootstrap_auth.py on the host (one-time).
Inside the container, `auth` subcommand just validates the existing token and
confirms it can reach the Gmail API.
"""
import logging
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def load_credentials(config_dir: str) -> Credentials:
    """Load and (if needed) refresh credentials from token.json."""
    token_path = Path(config_dir) / "token.json"
    if not token_path.exists():
        raise FileNotFoundError(
            f"token.json not found at {token_path}. "
            "Run bootstrap_auth.py on the host first to generate it."
        )
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Refreshing expired access token")
            creds.refresh(Request())
            _save_credentials(creds, token_path)
        else:
            raise RuntimeError(
                "Credentials are invalid and cannot be refreshed. "
                "Re-run bootstrap_auth.py on the host."
            )
    return creds


def _save_credentials(creds: Credentials, path: Path) -> None:
    path.write_text(creds.to_json())
    logger.info("Saved refreshed token", extra={"path": str(path)})


def verify_auth(config_dir: str) -> None:
    """Validate token and print the authenticated email address."""
    from googleapiclient.discovery import build

    creds = load_credentials(config_dir)
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    email = profile.get("emailAddress", "unknown")
    logger.info("Auth OK", extra={"email": email, "messages_total": profile.get("messagesTotal")})
    print(f"Authenticated as: {email}")
    print(f"Total messages reported by API: {profile.get('messagesTotal', 'unknown')}")
