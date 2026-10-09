#!/bin/bash
set -e

PROJECT_ID="bowling-subscriber"
SERVICE_NAME="bowling-email-subscriber"
REGION="us-central1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "Building container image..."
gcloud builds submit --tag "${IMAGE}" --project="${PROJECT_ID}"

echo "Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE}" \
  --platform=managed \
  --region="${REGION}" \
  --allow-unauthenticated \
  --set-env-vars="GMAIL_PUBSUB_TOPIC=projects/bowling-subscriber/topics/gmail-inbox" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,GMAIL_TOKEN_JSON=GMAIL_TOKEN_JSON:latest,GMAIL_PUSH_AUDIENCE=GMAIL_PUSH_AUDIENCE:latest,GMAIL_PUSH_SERVICE_ACCOUNT=GMAIL_PUSH_SERVICE_ACCOUNT:latest,GMAIL_WATCH_AUDIENCE=GMAIL_WATCH_AUDIENCE:latest,GMAIL_WATCH_SERVICE_ACCOUNT=GMAIL_WATCH_SERVICE_ACCOUNT:latest" \
  --project="${PROJECT_ID}"

echo "Getting service URL..."
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --format='value(status.url)' --project="${PROJECT_ID}")
echo "Service deployed to: ${SERVICE_URL}"
echo ""
echo "Update your Pub/Sub subscription with:"
echo "  gcloud pubsub subscriptions modify-push-config gmail-inbox-push \\"
echo "    --push-endpoint=\"\${SERVICE_URL}/gmail-subscriber/push\""
