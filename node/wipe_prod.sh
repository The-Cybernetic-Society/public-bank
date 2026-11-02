#!/bin/sh
# Remove a pool's businesses on PRODUCTION (keeps the pool, its round and the treasurer), or one business of it.
# Review publicbank/wipe_businesses.py first.
#   sh node/wipe_prod.sh goa-business-commons                                     # every business
#   sh node/wipe_prod.sh goa-business-commons goa-business-commons/test-shop      # one business
set -e
POOL="${1:?pool id}"
ONLY="${2:+--business $2}"
HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=cybernetic-society; REGION=asia-southeast1
IMG=$(gcloud run services describe publicbank --region "$REGION" --project "$PROJECT" --format='value(spec.template.spec.containers[0].image)')
B64=$(base64 < "$HERE/publicbank/wipe_businesses.py" | tr -d '\n')
# the job's image predates this file, so the script travels inside the command; '@@' is the argument delimiter
gcloud run jobs update publicbank-seed --region "$REGION" --project "$PROJECT" --image "$IMG" --command=sh \
  --args="^@@^-c@@cd /app && echo $B64 | base64 -d > publicbank/wipe_businesses.py && python -m publicbank.wipe_businesses --pool $POOL $ONLY --yes" --quiet
gcloud run jobs execute publicbank-seed --region "$REGION" --project "$PROJECT" --wait --quiet
sleep 8
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=publicbank-seed" --project "$PROJECT" --limit=30 --format='value(textPayload)' --freshness=3m | grep -E "businesses|Error|Traceback" | head -3
