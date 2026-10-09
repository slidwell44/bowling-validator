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
exactly `Test` or contains `Substitute bowler request`.

When a DAC substitute-bowler request arrives (`Substitute bowler request for ...`
from DAC Mail), the service extracts the accept URL from the message and completes
the acceptance automatically: headless Chromium (Playwright) opens the invite page,
clicks the Accept button, and verifies the confirmation. Invites for past dates
are skipped, and each event date is reserved in Neon before browser automation
starts so another request for that date cannot be accepted. An uncertain attempt
stays reserved for manual review rather than risking a duplicate acceptance.

Form automation requires Playwright Chromium in the runtime environment:

```bash
uv run playwright install chromium
```

On a blank Linux host, install browser OS dependencies with
`uv run playwright install --with-deps chromium`.

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
