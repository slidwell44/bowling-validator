# bowling-email-subscriber

FastAPI application for the bowling-validator project. It currently provides a
`/hello` route and redirects `/` to `/docs`; Gmail labels are available through
`/gmail-subscriber/labels` after mailbox authorization.

See the [repository README](../README.md) for environment variables, local and
development-container setup, project structure, and development commands.

## Authorize your mailbox

Enable the Gmail API and configure the OAuth consent screen in Google Cloud.
Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in your `.env`.
Use a Desktop OAuth client for this local setup flow. If using a Web application
client, register `http://localhost:8080/` as an authorized redirect URI.

From this directory, run:

```bash
uv run python -m scripts.oauth
```

Open the printed URL in your browser and approve Gmail read access. The command
explicitly requests offline access and consent, even if you previously approved
the app. It saves `gmail-token.json` only when Google returns a refresh token.
This file is ignored by Git and readable/writable only by its owner.

The callback uses `http://localhost:8080/`. In a remote dev container or Codespace,
forward port 8080 to localhost on the machine running your browser (for example,
through VS Code desktop). A public HTTPS forwarded URL is not the same callback.
Alternatively, run this setup locally and securely copy the generated token file
into this application directory on the server.

Verify authorization and fetch label names and IDs directly:

```bash
uv run python -m scripts.labels
```

Or start the API as usual and call `/gmail-subscriber/labels`. The Google client
uses the saved refresh token to obtain access tokens automatically. Run the setup
command again if access is revoked. This setup authorizes one mailbox for the
service; it does not implement per-user web login.
