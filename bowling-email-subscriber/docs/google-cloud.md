# Google Cloud Setup

This runbook configures Gmail push notifications for the deployed FastAPI
application. It assumes the Google Cloud project is `bowling-subscriber` and
the application URL is:

```text
https://bowling-email-subscriber.fastapicloud.dev
```

## Local Gmail authorization

Enable the Gmail API and configure the OAuth consent screen. Set these values
in the local `.env` file:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=your-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
```

Run the OAuth helper from `bowling-email-subscriber`:

```bash
uv run python -m scripts.oauth
```

Open the printed URL and approve read access. The callback is
`http://localhost:8080/`. In a remote development container, forward port 8080
to the machine running the browser. The command saves `gmail-token.json` only
when Google returns a refresh token.

Verify the mailbox locally with:

```bash
uv run python -m scripts.labels
```

## FastAPI Cloud settings

Connect the Neon PostgreSQL resource to the app. FastAPI Cloud should create
the encrypted `DATABASE_URL` secret automatically. The application requires
PostgreSQL whenever `GMAIL_TOKEN_JSON` is configured.

Configure these application secrets and variables:

| Name | Value |
| --- | --- |
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `GMAIL_TOKEN_JSON` | Complete contents of the local `gmail-token.json` |
| `GMAIL_PUBSUB_TOPIC` | `projects/bowling-subscriber/topics/gmail-inbox` |
| `GMAIL_PUSH_AUDIENCE` | `https://bowling-email-subscriber.fastapicloud.dev/gmail-subscriber/push` |
| `GMAIL_PUSH_SERVICE_ACCOUNT` | `gmail-push@bowling-subscriber.iam.gserviceaccount.com` |
| `GMAIL_WATCH_AUDIENCE` | `https://bowling-email-subscriber.fastapicloud.dev/gmail-subscriber/watch` |
| `GMAIL_WATCH_SERVICE_ACCOUNT` | `gmail-watch@bowling-subscriber.iam.gserviceaccount.com` |

Optionally configure `GMAIL_ADMIN_TOKEN` to protect the HTTP labels endpoint.
Do not commit or paste OAuth tokens or database URLs.

Deploy from the application directory:

```bash
uv run fastapi login
uv run fastapi deploy
```

## Google Cloud resources

Run these commands in Google Cloud Shell:

```bash
gcloud config set project bowling-subscriber
gcloud services enable gmail.googleapis.com pubsub.googleapis.com cloudscheduler.googleapis.com

gcloud pubsub topics create gmail-inbox
gcloud pubsub topics add-iam-policy-binding gmail-inbox \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
  --role=roles/pubsub.publisher

gcloud iam service-accounts create gmail-push --display-name="Gmail Pub/Sub delivery"
gcloud iam service-accounts create gmail-watch --display-name="Gmail watch renewal"

gcloud beta services identity create --service=pubsub.googleapis.com --project=bowling-subscriber
PROJECT_NUMBER=$(gcloud projects describe bowling-subscriber --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding \
  gmail-push@bowling-subscriber.iam.gserviceaccount.com \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator
```

The deploying account needs permission to create resources and
`iam.serviceAccounts.actAs` on both service accounts. No service-account key
file is required.

Create the authenticated Pub/Sub push subscription:

```bash
APP_URL=https://bowling-email-subscriber.fastapicloud.dev

gcloud pubsub subscriptions create gmail-inbox-push \
  --topic=gmail-inbox \
  --push-endpoint="${APP_URL}/gmail-subscriber/push" \
  --push-auth-service-account=gmail-push@bowling-subscriber.iam.gserviceaccount.com \
  --push-auth-token-audience="${APP_URL}/gmail-subscriber/push" \
  --ack-deadline=120
```

Keep Pub/Sub's default wrapped JSON payload. The API validates the signed
identity token, audience, and service account before reading Gmail.

Create and run the daily watch-renewal job:

```bash
gcloud scheduler jobs create http gmail-watch-renew \
  --location=us-central1 \
  --schedule="0 6 * * *" \
  --time-zone=Etc/UTC \
  --uri="${APP_URL}/gmail-subscriber/watch" \
  --http-method=POST \
  --oidc-service-account-email=gmail-watch@bowling-subscriber.iam.gserviceaccount.com \
  --oidc-token-audience="${APP_URL}/gmail-subscriber/watch" \
  --attempt-deadline=180s

gcloud scheduler jobs run gmail-watch-renew --location=us-central1
```

The watch endpoint should return HTTP 200 with a `historyId` and expiration.
Register the watch on the deployed endpoint so its cursor is stored in Neon.

The application creates these tables automatically in the configured database:

- `mailbox` stores the Gmail history cursor for the watched mailbox.
- `processed_messages` stores each successfully processed substitute-bowler
  request, including its Gmail message ID, mailbox, subject, body, history ID,
  and processing timestamp. The message ID is unique, so Pub/Sub retries do
  not create duplicate history rows or duplicate body output.

You can inspect the history from Neon SQL Editor:

```sql
SELECT message_id, mailbox_email, subject, body, processed_at
FROM processed_messages
ORDER BY processed_at DESC;
```

## Behavior and recovery

The watch observes new `INBOX` messages. The application logs the body when the
`Subject` header is exactly `Test` or contains `Substitute bowler request`,
case-insensitively. It prefers plain text and falls back to HTML source.
Pub/Sub notifications contain mailbox history, not the email subject, so
Pub/Sub filters cannot filter by subject or body.

HTTP 204 acknowledges a notification. Non-success responses cause Pub/Sub to
retry. PostgreSQL preserves the cursor across deployments and serializes
concurrent deliveries.

If Gmail history expires, reset the cursor intentionally with matching cloud
credentials:

```bash
uv run python -m scripts.watch --reset-history
```

Reauthorize locally and replace `GMAIL_TOKEN_JSON` if the OAuth refresh token
expires.

## References

- [Gmail push notifications](https://developers.google.com/workspace/gmail/api/guides/push)
- [Pub/Sub authenticated push](https://docs.cloud.google.com/pubsub/docs/authenticate-push-subscriptions)
- [Cloud Scheduler authentication](https://docs.cloud.google.com/scheduler/docs/http-target-auth)
- [FastAPI Cloud integrations](https://fastapicloud.com/docs/integrations/third-party-integrations/)
