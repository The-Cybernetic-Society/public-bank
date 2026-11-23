#!/bin/bash
# The deploy step of node/cloudbuild.yaml: roll the image just built out to Cloud Run. Settings arrive as environment
# variables from the build's substitutions (Cloud Build reads $NAME inside its own file as a substitution, so the shell
# lives here). Secrets are mounted by name; none is read here.
set -euo pipefail
CONN=$(gcloud sql instances describe publicbank --format='value(connectionName)')
EXTRA=""
for s in ANTHROPIC_API_KEY PB_WA_TOKEN PB_WA_APP_SECRET PB_WA_VERIFY_TOKEN; do
  if gcloud secrets describe "$s" >/dev/null 2>&1; then EXTRA="$EXTRA,$s=$s:latest"; fi
done
ENV="PB_BASE_URL=$BASE_URL,PB_OLD_HOSTS=$OLD_HOSTS,PB_BOT_USERNAME=cybernetic_society_bot,EDGEOS_API_BASE=https://api.edgeos.world"
# the WhatsApp number, once Meta has given one (the trigger's _WA_PHONE_ID / _WA_NUMBER substitutions); the templates by name
if [ -n "${WA_PHONE_ID:-}" ]; then
  ENV="$ENV,PB_WA_PHONE_ID=$WA_PHONE_ID,PB_WA_NUMBER=$WA_NUMBER,PB_WA_POOL=${WA_POOL:-goa-business-commons},PB_WA_OTP_TEMPLATE=commons_code,PB_WA_TEMPLATE=commons_notice"
fi
if [ -n "${EXTRA_ENV:-}" ]; then ENV="$ENV,$EXTRA_ENV"; fi
gcloud run deploy "$SERVICE" --image "$IMAGE" --region="$REGION" --allow-unauthenticated \
  --add-cloudsql-instances="$CONN" --min-instances="$MIN_INSTANCES" --max-instances=3 --memory=512Mi \
  --set-env-vars="$ENV" \
  --set-secrets="DATABASE_URL=$DB_SECRET:latest,PB_SECRET=PB_SECRET:latest,PB_APP_TOKEN=PB_APP_TOKEN:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest$EXTRA" --quiet
# the one-off job runs the code just deployed, so "No module named …" never follows a deploy again
if [ "$SERVICE" = "publicbank" ]; then
  gcloud run jobs update publicbank-seed --region="$REGION" --image "$IMAGE" --quiet || true
fi
gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)'
