#!/usr/bin/env bash
# One-time GCP setup + deploy for the Public Bank node. Run from node/. Re-run `gcloud run deploy` alone for updates.
set -euo pipefail
PROJECT=${PROJECT:?set PROJECT=your-gcp-project-id}
REGION=${REGION:-asia-southeast1}        # Singapore: custom-domain mapping supported; ~60 ms from Goa
BASE_URL=${BASE_URL:-https://bank.cyberneticsutra.com}
DBPASS=$(openssl rand -hex 16)

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# Postgres (smallest shared-core instance)
gcloud sql instances create publicbank --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=db-f1-micro --region="$REGION" --storage-size=10 --storage-type=HDD || true
gcloud sql databases create publicbank --instance=publicbank || true
gcloud sql users create pb --instance=publicbank --password="$DBPASS"
CONN=$(gcloud sql instances describe publicbank --format='value(connectionName)')

# Secrets
printf '%s' "postgresql://pb:${DBPASS}@/publicbank?host=/cloudsql/${CONN}" | gcloud secrets create DATABASE_URL --data-file=- || true
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create PB_SECRET --data-file=- || true
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create PB_APP_TOKEN --data-file=- || true
printf '%s' "${TELEGRAM_BOT_TOKEN:?set TELEGRAM_BOT_TOKEN}" | gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=- || true

# Service (always one warm instance so the bot and the agents' MCP calls never hit a cold start)
gcloud run deploy publicbank --source . --region="$REGION" --allow-unauthenticated \
  --add-cloudsql-instances="$CONN" --min-instances=1 --max-instances=3 --memory=512Mi \
  --set-env-vars="PB_BASE_URL=${BASE_URL},PB_BOT_USERNAME=${PB_BOT_USERNAME:-PublicBankBot},EDGEOS_API_BASE=https://api.edgeos.world" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,PB_SECRET=PB_SECRET:latest,PB_APP_TOKEN=PB_APP_TOKEN:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest"

# Custom domain → prints the DNS record to add
gcloud beta run domain-mappings create --service=publicbank --domain="${BASE_URL#https://}" --region="$REGION" || true
echo; echo "PB_APP_TOKEN (for the mini-app registration):"; gcloud secrets versions access latest --secret=PB_APP_TOKEN; echo
