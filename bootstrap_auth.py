"""
One-time host-side OAuth bootstrap.

Run this ONCE on your host machine (not in Docker) to produce ./config/token.json.
After that, all Docker runs load the saved token automatically.

Prerequisites:
  pip install google-auth-oauthlib

Usage:
  python bootstrap_auth.py [--config-dir ./config]
"""
import argparse
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    parser = argparse.ArgumentParser(description="Bootstrap Gmail OAuth token")
    parser.add_argument(
        "--config-dir",
        default=os.environ.get("CONFIG_DIR", "./config"),
        help="Directory containing credentials.json; token.json will be written here",
    )
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)

    creds_path = config_dir / "credentials.json"
    token_path = config_dir / "token.json"

    if not creds_path.exists():
        print(f"ERROR: {creds_path} not found.")
        print(
            "Download it from the Google Cloud Console:\n"
            "  APIs & Services → Credentials → your OAuth 2.0 Client ID → Download JSON\n"
            f"Save it as: {creds_path}"
        )
        raise SystemExit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    # run_local_server opens the browser and handles the redirect automatically.
    creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    print(f"\ntoken.json written to: {token_path}")
    print("You can now run the scraper in Docker.")


if __name__ == "__main__":
    main()
