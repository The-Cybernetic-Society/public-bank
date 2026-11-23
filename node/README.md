# Public Bank · node service

A treasury for a community that wants to decide together how to spend a pot, and to federate with
other communities under rules everyone can read. One service hosts many **nodes** (a residency, a
village pool, a standing community). Each node runs **rounds**:

```
proposals → ballots (100 points each) → plans (Equal Shares + 3 others) → ranking (Schulze)
         → approval (≥60% of eligible members) → payouts (UPI, by a human treasurer) → export
```

Money moves only after a ratified decision, only as a purchase or a reimbursement against a receipt,
and only by a person tapping a `upi://` link. The service never holds funds and never calls a bank.

## Start here

The product is this one service: the data, the WhatsApp side, the public board and the signed-in page at `/app/`.
cyberneticsociety.com is a separate static brochure that only links here.

```bash
# run it locally (SQLite, no secrets needed)
cd node && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
PB_OTP_DEV=1 uvicorn publicbank.main:app --port 8000      # http://localhost:8000  ·  the Commons page: /app/
python -m publicbank.demo --stage proposals --pool goa-business-commons --treasurer-telegram 1   # a pool with six businesses to play with

# test (every suite, about four minutes; CI runs the same on every push)
for t in db round web bot webhook whatsapp pool apply twin summary contact app templates; do python tests/test_$t.py; done

# deploy: merge to main. Cloud Build builds the image and rolls it out to Cloud Run; a merge to `staging` does the same to
# the staging service. Roll back from the Cloud Run console (Revisions → the previous one → Manage traffic). Details below.
```

`PB_OTP_DEV=1` shows the WhatsApp sign-in code on screen instead of sending it; it is for local and staging only.
With no `ANTHROPIC_API_KEY` the twin groups needs by word overlap instead of writing drafts; with the key it writes them.

## Surfaces

| Surface | For | Where |
|---|---|---|
| Telegram bot | residents (join via QR, get notified), treasurer (payout queue) | `publicbank/bot.py` |
| WhatsApp | businesses and groups whose members live on WhatsApp: owner cards, team links, the round as chat | `publicbank/whatsapp.py` |
| Web | propose, ballot, rank, approve, coordinator & treasury pages, export | `publicbank/web.py` |
| MCP mini-app | residents' agents in Agent Village (confirmed writes only) | `publicbank/mcp_server.py`, `manifest.json`, `skills/publicbank/SKILL.md` |

Identity is pluggable and recorded per member (`verified_by`): a join link / QR (`deeplink`), a
coordinator's approval (`coordinator`), Edge City third-party sign-in (`edgeos_login`, enabled by
`EDGEOS_APP_KEY`), the mini-app dispatcher's `X-Village-Subject` (`miniapp`), or the phone number
WhatsApp verified (`whatsapp`). No directory is ever
read; nothing about a resident enters the system except what they or their coordinator put there.

## Run

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && edit .env
set -a; . ./.env; set +a

# seed a village: pool + residencies + rounds + treasurer + QR posters
python -m publicbank.setup_village --village goa-2026 --name "Edge City Goa 2026" \
  --residency "Residency A" --residency "Residency B" \
  --budget-per-residency 100000 --levy 20 --treasurer-telegram <your tg id> --qr out/

PB_BOT_POLLING=1 uvicorn publicbank.main:app --port 8000   # web + /mcp + bot (polling locally)
```

Locally the store is SQLite. In production set `DATABASE_URL` (Postgres) and the same schema is created
there; nothing else changes. The bot runs on a webhook (`/tg/<secret>`, registered at startup from
`PB_BASE_URL`), so the service is stateless and can restart or scale without missing updates.

`out/*.png` are posters: one QR per residency. Scanning opens `/j/<token>`, which creates a pending
membership and hands off to the bot. The coordinator approves on `/c/<node>`; from then on the
resident is notified in Telegram when ballots, plans and approval open.

## Deploy

**Normal path: merge to main.** A Cloud Build trigger watches the repository; on a push to `main` it runs `node/cloudbuild.yaml`,
which builds `node/Dockerfile`, pushes the image to Artifact Registry, deploys it to the Cloud Run service `publicbank` with
the same flags as before, and points the one-off `publicbank-seed` job at the same image. A push to `staging` does the same to
`publicbank-staging`, which has its own database and the development sign-in flag on, so a change can be seen live before
it reaches the shops. GitHub Actions runs the thirteen test suites on every push (`.github/workflows/test.yml`). The build does not wait for them, so
`main` is protected on GitHub: a merge needs a pull request with the `tests` check green. That is the whole safety gate. Secrets never live in the repo: the service reads them from Secret Manager by name.

Rollback: Cloud Run console → the service → Revisions → pick the previous revision → Manage traffic → 100%. Logs: the same
console, Logs tab. Nothing needs doing to the database on a deploy; the schema migrates itself when the service starts.

**One-time setup, from an account that owns the project** (done once, kept here for the record):

```bash
PROJECT=cybernetic-society; REGION=asia-southeast1
gcloud config set project $PROJECT
gcloud services enable cloudbuild.googleapis.com artifactregistry.googleapis.com run.googleapis.com
gcloud artifacts repositories create publicbank --repository-format=docker --location=$REGION
# let Cloud Build deploy to Cloud Run and act as the runtime service account
PN=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
for role in roles/run.admin roles/iam.serviceAccountUser roles/cloudsql.viewer roles/secretmanager.viewer; do
  gcloud projects add-iam-policy-binding $PROJECT --member="serviceAccount:$PN@cloudbuild.gserviceaccount.com" --role=$role; done
# connect the GitHub repository to Cloud Build: console → Cloud Build → Repositories → the "2nd gen" tab (region asia-southeast1) →
# Create host connection (installs the "Cloud Build" GitHub app; give it public-bank only) → Link repository → public-bank.
# Do NOT use the Cloud Run console's "add repository" screen: that is the first-generation integration, a different GitHub app,
# and it cannot see a second-generation connection. Then the two triggers, against the linked repository's resource name:
gcloud builds connections list --region=$REGION                    # the connection's name
REPO=projects/$PROJECT/locations/$REGION/connections/CONNECTION_NAME/repositories/public-bank
gcloud builds triggers create github --name=publicbank-main --repository=$REPO \
  --branch-pattern='^main$' --build-config=node/cloudbuild.yaml --region=$REGION
gcloud builds triggers create github --name=publicbank-staging --repository=$REPO \
  --branch-pattern='^staging$' --build-config=node/cloudbuild.yaml --region=$REGION \
  --substitutions=_SERVICE=publicbank-staging,_DB_SECRET=DATABASE_URL_STAGING,_EXTRA_ENV=PB_OTP_DEV=1,_MIN_INSTANCES=0,_OLD_HOSTS=,_BASE_URL=https://publicbank-staging-PLACEHOLDER.a.run.app
# staging's own database on the same instance, and its connection string as a secret
gcloud sql databases create publicbank_staging --instance=publicbank
DBPASS=$(gcloud secrets versions access latest --secret=DATABASE_URL | sed 's#.*pb:\([^@]*\)@.*#\1#')
CONN=$(gcloud sql instances describe publicbank --format='value(connectionName)')
printf '%s' "postgresql://pb:${DBPASS}@/publicbank_staging?host=/cloudsql/${CONN}" | gcloud secrets create DATABASE_URL_STAGING --data-file=-
# after the first staging deploy, put its real URL into the trigger's _BASE_URL (gcloud run services describe publicbank-staging --format='value(status.url)')
# a developer who deploys by merging needs no cloud role at all; to read logs and roll back from the console:
gcloud projects add-iam-policy-binding $PROJECT --member="user:DEV_EMAIL" --role=roles/run.developer
gcloud projects add-iam-policy-binding $PROJECT --member="user:DEV_EMAIL" --role=roles/logging.viewer
# the address move, when ready: map the new domain (prints the CNAME to add at the DNS host), wait for it to serve, then change the two
# address lines at the top of node/cloudbuild.yaml (_BASE_URL to the new host, _OLD_HOSTS to the old ones) and merge; the old hosts forward
gcloud beta run domain-mappings create --service=publicbank --domain=goa.cyberneticsociety.com --region=$REGION
```

`sh node/deploy.sh` still deploys by hand from a laptop with `gcloud` signed in, for the day Cloud Build is broken.

## WhatsApp (businesses, self-help groups, wards)

Set the `PB_WA_*` variables from `.env.example` (a Meta business app, a phone number not already on WhatsApp,
the webhook at `https://<host>/wa`). Then seed a federation:

```bash
python -m publicbank.setup_businesses --pool goa-biz-2026 --name "Goa Business Pool" --list businesses.txt \
    --treasurer-telegram <your tg id> --csv cards.csv
```

Each business gets an owner's card: a `wa.me` link (also a printable card at `/j/<token>/poster.png?via=wa`)
that opens a chat with `JOIN <code>` prefilled. The first person to send it becomes that business's
coordinator, is shown the pool's charter (`PB_WA_CHARTER`), and on accepting gets a link to forward to their
team; anyone tapping that joins straight away. A business not on the list sends `JOIN` alone and names
itself (needs `PB_WA_POOL`). From then on the round runs in chat: `STATUS`, `PROPOSE`, `VOTE`, `RANK`,
`APPROVE`, with buttons for yes/no and a one-tap link to the web page for the ballot and the plans.
Replies inside a member's 24-hour window are free text; a round announcement outside it goes as the
approved template named by `PB_WA_TEMPLATE`.

## A federation of businesses (the pool round)

A pool node with children runs its own round. Any member of a child may propose; in a pool with `cosign_min`,
that many children must co-sign (their coordinators, on the page or by replying COSIGN) before a proposal
reaches the ballot. With `pool_vote='nodes'` each child casts one ballot, through its coordinator. Children
put money in with `CONTRIBUTE 500`; a pool with `match_pct` matches it (500 = 5x) up to its own pot, which is
then the pool's spendable budget. Ratification is per child at the child's own threshold, and the pool's
decision passes only when every child that accepted the charter passes. The export carries the per-child
tallies. The twin (`python -m publicbank.twin --pool <id>`) turns recorded needs into draft proposals.

The **live board** at `/b/<node>` is public and read-only: stage, pot, contributions, needs, proposals with
their backers, ballots in, plans, approval per node, the decision and the ledger, refreshing every 15 s.
No names, phone numbers or UPI ids appear on it. `python -m publicbank.demo --stage paid` builds a six-business
rehearsal in whatever database the service uses.

## Round, step by step

1. **Proposals.** Anyone active in the node proposes: title, text, ideal ₹, minimum ₹, and whether they
   will lead it. A proposal needs a lead to be eligible. Agents can draft; a human confirms.
2. **Freeze → ballots.** Coordinator freezes the list. Each member splits 100 points across items.
3. **Plans.** Four candidate plans are computed from the same ballots: Equal Shares (with a receipt
   per member showing where their equal share went), Nash, utilitarian, maximin. Plans that can't
   spend the budget validly are marked so. If ideals add up to less than the pot, the rest stays in
   the pot.
4. **Ranking → winner.** Members rank the valid plans; Schulze picks one. If nobody ranks, Equal
   Shares wins.
5. **Approval.** At least 60% of *eligible* members must explicitly approve. Abstention is not approval.
6. **Payouts.** Each funded item becomes a queued payout to its lead's UPI id (or a vendor's). The
   treasurer taps the link in Telegram or on `/treasury`, pays from their own bank app, and records
   the reference. Receipts are kept outside the system.
7. **Export.** `/export/<node>` emits the five protocol objects (node, offers, proposals, decision,
   tally) with no identities and no UPI ids, for anyone to audit or for another node to read.

## Federation (the part that recurses)

A node may have a parent. When a child opens a round, `levy_pct` of its pot is escrowed as a
contribution to the parent's round, where it is spent by the parent's own members under the same
rules. Rules of the parent are fixed before any child joins: levy fraction, weight = member count,
and a parent decision pays out only when every affected child has ratified. A node joins for a
round and leaves by not renewing. That is the whole federation mechanism; it is the same code at
every level.

## Tests

```bash
python tests/test_round.py   # core: 12 members, 6 proposals, 4 plans, Schulze, ratification, payouts, levy
python tests/test_web.py     # web: join → propose → ballot → plans → rank → approve → treasury
python tests/test_bot.py     # bot: deep-link join, login link, payout queue with upi://, paid ref
python tests/test_webhook.py # webhook: secret + header checked, update dispatched
python tests/test_whatsapp.py # WhatsApp: owner card → charter, team link, self-registration, the round in chat, templates, signature
# same suites against Postgres: DATABASE_URL=postgresql://... python tests/test_round.py
```

## Hard rules in code

- A resident's Edge City token is used once, for `/humans/me`, and dropped. Never stored.
- No Edge City directory reads. No reads of agent memory or context from the MCP side.
- Every MCP write needs `Authorization: Bearer <PB_APP_TOKEN>` + a 32-hex `X-Village-Subject`, and
  replays by `Idempotency-Key`. Missing either → `unauthorized`.
- Amounts are integer paise. Points sum to 100. Approval counts eligible members, not voters.
- Exports carry no names, Telegram ids, Edge City ids or UPI ids.

MIT. The Cybernetic Society.
