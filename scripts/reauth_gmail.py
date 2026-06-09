#!/usr/bin/env python3
"""One-off interactive Gmail OAuth re-consent for the emailsync daemons.

Run this on the host whenever the refresh token dies (symptom: emailsync
services crash-loop with `webbrowser.Error: could not locate runnable browser`).

Both the work and personal daemons authenticate the SAME Google account via a
single shared token at ~/.emailsync/token.json, so one run fixes both.

WSL note: this never launches a browser itself (that is what crashed the
daemon). It prints the consent URL — open it in your Windows browser, approve,
and Google redirects to http://localhost:<PORT>, which WSL2 localhost
forwarding delivers back to the listener here.
"""

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_PATH = Path("~/.emailsync/credentials.json").expanduser()
TOKEN_PATH = Path("~/.emailsync/token.json").expanduser()
PORT = 8765  # fixed so the redirect URI is predictable


def main() -> None:
    if not CREDENTIALS_PATH.exists():
        sys.exit(f"Credentials not found: {CREDENTIALS_PATH}")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)

    print("\n=== Gmail re-consent ===")
    print("1. Open the URL below in your Windows browser.")
    print("2. Log in to the Gmail account the daemons sync.")
    print("3. Approve read-only access.")
    print(f"4. You will be redirected to http://localhost:{PORT}/ (this listener).\n")

    creds = flow.run_local_server(
        port=PORT,
        open_browser=False,
        access_type="offline",  # request a refresh_token
        prompt="consent",  # force re-issue of refresh_token
        authorization_prompt_message="Open this URL to authorize:\n\n{url}\n",
        success_message="Done. You can close this tab and return to the terminal.",
    )

    if not creds.refresh_token:
        sys.exit(
            "ERROR: no refresh_token returned. Revoke prior grant at "
            "https://myaccount.google.com/permissions and re-run."
        )

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    TOKEN_PATH.chmod(0o600)
    print(f"\nToken written: {TOKEN_PATH}")


if __name__ == "__main__":
    main()
