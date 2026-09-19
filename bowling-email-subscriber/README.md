# Bowling Email Subscriber

The Bowling Email Subscriber is the FastAPI service that connects the
bowling-validator project to one authorized Gmail mailbox.

It provides:

- a health endpoint at `/hello`;
- a protected Gmail labels endpoint at `/gmail-subscriber/labels`;
- authenticated Gmail watch renewal at `/gmail-subscriber/watch`;
- authenticated Pub/Sub delivery at `/gmail-subscriber/push`.

The service tracks Gmail history in PostgreSQL in production and SQLite during
local development. It currently logs the body of new inbox messages whose
subject is exactly `Test`; form automation is planned but not implemented.

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
