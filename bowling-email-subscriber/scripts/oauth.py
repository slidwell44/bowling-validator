import os
from pathlib import Path
from typing import NewType

import google.auth.external_account_authorized_user
import google.oauth2.credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import Settings

TOKEN_FILE: Path = Path(__file__).resolve().parents[1] / "gmail-token.json"
SCOPES: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]

ExternalCredentials = NewType(
    "ExternalCredentials",
    google.auth.external_account_authorized_user.Credentials,
)
Oauth2Credentials = NewType(
    "Oauth2Credentials",
    google.oauth2.credentials.Credentials,
)


def main() -> None:
    from config import get_settings

    settings: Settings = get_settings()
    flow: InstalledAppFlow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": settings.google.CLIENT_ID,
                "client_secret": settings.google.CLIENT_SECRET.get_secret_value(),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8080/"],
            }
        },
        SCOPES,
    )
    credentials: ExternalCredentials | Oauth2Credentials = flow.run_local_server(
        host="localhost",
        port=8080,
        open_browser=False,
        access_type="offline",
        prompt="consent",
        timeout_seconds=300,
    )
    if not credentials.refresh_token:
        raise RuntimeError("Google did not return a refresh token; no file was saved.")

    fd: int = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as token_file:
        os.fchmod(token_file.fileno(), 0o600)
        token_file.write(credentials.to_json())
    print(f"Gmail authorization saved to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
