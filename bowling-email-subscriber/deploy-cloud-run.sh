#!/bin/bash
set -e

PROJECT_ID="bowling-subscriber"
SERVICE_NAME="bowling-email-subscriber"
REGION="us-central1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "Building container image..."
gcloud builds submit --tag "${IMAGE}" --project="${PROJECT_ID}"

echo "Ensuring LOGFIRE_TOKEN secret exists..."
if ! gcloud secrets describe LOGFIRE_TOKEN --project="${PROJECT_ID}" >/dev/null 2>&1; then
  if [ -z "${LOGFIRE_TOKEN:-}" ]; then
    echo "ERROR: LOGFIRE_TOKEN secret is missing in Secret Manager and no" >&2
    echo "       LOGFIRE_TOKEN environment variable is set to create it from." >&2
    exit 1
  fi
  printf '%s' "${LOGFIRE_TOKEN}" | gcloud secrets create LOGFIRE_TOKEN \
    --data-file=- --project="${PROJECT_ID}"
fi

echo "Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE}" \
  --platform=managed \
  --region="${REGION}" \
  --allow-unauthenticated \
  --memory=1Gi \
  --cpu=1 \
  --timeout=300 \
  --set-env-vars="GMAIL_PUBSUB_TOPIC=projects/bowling-subscriber/topics/gmail-inbox" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,GMAIL_TOKEN_JSON=GMAIL_TOKEN_JSON:latest,GMAIL_PUSH_AUDIENCE=GMAIL_PUSH_AUDIENCE:latest,GMAIL_PUSH_SERVICE_ACCOUNT=GMAIL_PUSH_SERVICE_ACCOUNT:latest,GMAIL_WATCH_AUDIENCE=GMAIL_WATCH_AUDIENCE:latest,GMAIL_WATCH_SERVICE_ACCOUNT=GMAIL_WATCH_SERVICE_ACCOUNT:latest,GMAIL_ADMIN_TOKEN=GMAIL_ADMIN_TOKEN:latest,LOGFIRE_TOKEN=LOGFIRE_TOKEN:latest" \
  --project="${PROJECT_ID}"

echo "Getting service URL..."
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --format='value(status.url)' --project="${PROJECT_ID}")
echo "Service deployed to: ${SERVICE_URL}"
echo ""
echo "Update your Pub/Sub subscription with:"
echo "  gcloud pubsub subscriptions modify-push-config gmail-inbox-push \\"
echo "    --push-endpoint=\"\${SERVICE_URL}/gmail-subscriber/push\""
