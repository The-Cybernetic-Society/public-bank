#!/bin/sh
# Deploy the node to Cloud Run. Run from anywhere: `sh node/deploy.sh`. Needs `gcloud auth login` first.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=cybernetic-society
REGION=asia-southeast1
CONN=$(gcloud sql instances describe publicbank --project "$PROJECT" --format='value(connectionName)')
gcloud run deploy publicbank --project "$PROJECT" --source "$HERE" --region="$REGION" --allow-unauthenticated \
  --add-cloudsql-instances="$CONN" --min-instances=1 --max-instances=3 --memory=512Mi \
  --set-env-vars="PB_BASE_URL=https://bank.cyberneticsutra.com,PB_BOT_USERNAME=cybernetic_society_bot,EDGEOS_API_BASE=https://api.edgeos.world" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,PB_SECRET=PB_SECRET:latest,PB_APP_TOKEN=PB_APP_TOKEN:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest" \
  --quiet
