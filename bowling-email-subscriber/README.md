# Bowling Email Subscriber

The Bowling Email Subscriber is the FastAPI service that connects the
bowling-validator project to one authorized Gmail mailbox.

It provides:

- a health endpoint at `/hello`;
- a protected Gmail labels endpoint at `/gmail-subscriber/labels`;
- authenticated Gmail watch renewal at `/gmail-subscriber/watch`;
- authenticated Pub/Sub delivery at `/gmail-subscriber/push`.

The service tracks Gmail history in PostgreSQL in production and SQLite during
local development. It logs the body of new inbox messages whose subject is
exactly `Test` or contains `Substitute bowler request`; form automation is
planned but not implemented.
local development. It currently logs the body of new inbox messages whose
subject is exactly `Test`.

When a DAC substitute-bowler request arrives (`Substitute bowler request for ...`
from DAC Mail), the service extracts the accept URL from the message and completes
the acceptance automatically: headless Chromium (Playwright) opens the invite page,
clicks the Accept button, and verifies the confirmation. Invites for past dates are
skipped, and each accepted Gmail message ID is recorded so a Pub/Sub redelivery
never double-accepts. A failed accept leaves the history cursor in place so the
next delivery retries.

Form automation needs Playwright's Chromium on the host (including in production):

```bash
uv run playwright install chromium
```

plus the usual OS dependencies (`playwright install --with-deps` on a blank VM).

See [Google Cloud setup](docs/google-cloud.md) for OAuth, Gmail, Pub/Sub,
Neon, FastAPI Cloud, and Cloud Scheduler configuration.

## Development

From this directory:

```bash
uv sync --locked
uv run fastapi dev main.py
```

Run the quality checks with:

```bash
uv run python -m unittest gmail_subscriber.test_notifications
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
```
