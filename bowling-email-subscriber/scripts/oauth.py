import os
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, make_server

from google_auth_oauthlib.flow import InstalledAppFlow

from config import GoogleSettings

TOKEN_FILE: Path = Path(__file__).resolve().parents[1] / "gmail-token.json"
SCOPES: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]


class _Callback:
    def __init__(self) -> None:
        self.state: str = ""
        self.query: str | None = None

    def __call__(self, environ, start_response):
        query = environ.get("QUERY_STRING", "")
        params = parse_qs(query)
        if (
            environ["PATH_INFO"] != "/"
            or params.get("state") != [self.state]
            or not (params.get("code") or params.get("error"))
        ):
            start_response("400 Bad Request", [("Content-Type", "text/plain")])
            return [
                b"Not the current OAuth callback. Use the latest terminal authorization URL."
            ]
        self.query = query
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"Callback received. Check the terminal for the authorization result."]


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        # Callback URLs contain authorization codes; keep them out of logs.
        pass


def authorize(flow: InstalledAppFlow, port: int = 8080, timeout: float = 300):
    callback = _Callback()
    with make_server(
        "localhost", port, callback, handler_class=_QuietHandler
    ) as server:
        flow.redirect_uri = f"http://localhost:{server.server_port}/"
        url, callback.state = flow.authorization_url(
            access_type="offline", prompt="consent"
        )
        print(f"Please visit this URL to authorize this application: {url}", flush=True)
        deadline = monotonic() + timeout
        while callback.query is None:
            server.timeout = max(0, deadline - monotonic())
            if server.timeout == 0:
                raise TimeoutError(
                    "No matching Google callback arrived. Forward port 8080 to localhost:8080 "
                    "on your browser's machine, rerun, and open the newly printed URL."
                )
            server.handle_request()

    # Match the library's loopback HTTPS normalization for OAuthlib's URI parser.
    flow.fetch_token(
        authorization_response=f"https://localhost:{server.server_port}/?{callback.query}"
    )
    return flow.credentials


def main() -> None:
    settings = GoogleSettings()
    flow: InstalledAppFlow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": settings.CLIENT_ID,
                "client_secret": settings.CLIENT_SECRET.get_secret_value(),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8080/"],
            }
        },
        SCOPES,
    )
    credentials = authorize(flow)
    if not credentials.refresh_token:
        raise RuntimeError("Google did not return a refresh token; no file was saved.")

    fd: int = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as token_file:
        os.fchmod(token_file.fileno(), 0o600)
        token_file.write(credentials.to_json())
    print(f"Gmail authorization saved to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
