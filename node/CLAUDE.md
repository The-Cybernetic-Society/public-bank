# CLAUDE.md — Public Bank node

Read this before touching anything. It is the context a previous session built up; the code is the truth
where the two disagree.

## What this is

A treasury for a community that decides together how to spend a pot, under rules anyone can read, and
that federates: a node (residency) can belong to a parent node (village pool) by sending a fixed levy per
round. One service hosts many nodes. The long-term aim is a protocol for participatory budgeting that
recurses (residency → village → network of villages, Bhutan's GMC, future Edge Cities). Edge City Goa 2026
is the first deployment: four residency nodes (Ambhasi, Build3, Creators, CIMC) under a village pool, run
by The Cybernetic Society, not by Edge City.

Round: proposals → 100-point ballots → 4 candidate plans (Equal Shares with per-member receipts, Nash,
utilitarian, maximin) → ranking (Schulze; MES wins if nobody ranks) → approval by ≥60% of ELIGIBLE
members (abstention ≠ approval) → payout queue → a human treasurer pays via `upi://` link → export.

## Layout

- `publicbank/core.py` — every operation, transport-agnostic. All business rules live here. `PBError(code)`.
- `publicbank/db.py` — schema + SQLite/Postgres shim. `DATABASE_URL` set → Postgres (prod); else SQLite.
  Money is integer paise. All ints are BIGINT (Telegram ids overflow int32).
- `publicbank/cns_allocate.py` — the allocator (shared with NYU's copy in `edge-city-nyu`; keep in sync,
  don't fork its semantics). Core clamps the budget to Σideal before calling it (rollover note).
- `publicbank/web.py` — FastAPI pages: `/j/<token>` join, `/j/<token>/poster.png`, `/l/<token>` login
  from bot, `/signin` (EdgeOS third-party auth; hidden until `EDGEOS_APP_KEY`), `/me`, `/n/<node>` resident,
  `/c/<node>` coordinator (approve members, advance round → DMs everyone), `/treasury`, `/export/<node>`.
- `publicbank/bot.py` — Telegram: `/start j_<token>` join, `/start l_<token>` link, `/me`, `/status`,
  `/payouts` (treasurer: upi:// button + "Mark paid" → reference). Residents are identified by numeric
  Telegram id only.
- `publicbank/mcp_server.py` — Agent Village mini-app: 6 tools `publicbank_*`. Auth = `Authorization:
  Bearer PB_APP_TOKEN` + 32-hex `X-Village-Subject`; replays by `Idempotency-Key`. Fails closed.
- `publicbank/main.py` — one ASGI app: `/mcp` → MCP, `/tg/<secret>` → Telegram webhook, rest → web.
  Webhook is registered at startup from `PB_BASE_URL`. `PB_BOT_POLLING=1` for local dev.
- `publicbank/setup_village.py` — seed: pool + residencies + rounds + treasurer + join links.
- `manifest.json`, `skills/publicbank/SKILL.md` — mini-app manifest v2 and the agent skill (sha256 pinned
  in the manifest; re-pin if SKILL.md changes).
- `tests/test_{round,web,bot,webhook}.py` — plain scripts, `python tests/test_x.py`; each prints `… OK`.
  Run all four before any deploy. They also run against Postgres with `DATABASE_URL` set.

## Hard rules (do not relax)

- Never read the Edge City directory or any resident data the resident did not put here themselves.
  Edge City's team said so explicitly (consent terms). A resident's EdgeOS token is used once, for
  `/humans/me`, and dropped.
- The MCP side never reads an agent's memory/context. Every write is confirmed by the human (see SKILL.md).
- Exports carry no names, Telegram ids, EdgeOS ids or UPI ids.
- Approval counts eligible members, not voters. Points sum to 100. Amounts are paise.
- No function in core trusts a caller-supplied identity; transports verify, core receives `member_id`.
- Payouts are purchases or reimbursements against receipts, paid by a person tapping a link. The
  service never holds or moves money and never calls a bank.

## Identity of the author (important)

Commits and anything public are by **Valmiki the Younger <valmiki_the_younger@proton.me>** — the git
`includeIf` config in `~/.gitconfig-tcs` sets this for this folder. The author's real name must not appear
in this repo, its history, docs, or commit trailers. `.claude/settings.json` disables Co-Authored-By; keep
it that way and add no AI-attribution trailers. Prior commits use a fixed timestamp convention
(`GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` at 12:00 +05:30 of the day); follow it.

## Deploy (GCP, project `cybernetic-society`, region `asia-southeast1`)

Live at https://bank.cyberneticsutra.com (Cloud Run `publicbank`, Cloud SQL Postgres `publicbank`,
secrets in Secret Manager: `DATABASE_URL`, `PB_SECRET`, `PB_APP_TOKEN`, `TELEGRAM_BOT_TOKEN`).
Bot: `@cybernetic_society_bot`. Treasurer Telegram id is in the seed command history.

```bash
# deploy (from node/)
CONN=$(gcloud sql instances describe publicbank --format='value(connectionName)')
gcloud run deploy publicbank --source . --region=asia-southeast1 --allow-unauthenticated \
  --add-cloudsql-instances="$CONN" --min-instances=1 --max-instances=3 --memory=512Mi \
  --set-env-vars="PB_BASE_URL=https://bank.cyberneticsutra.com,PB_BOT_USERNAME=cybernetic_society_bot,EDGEOS_API_BASE=https://api.edgeos.world" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,PB_SECRET=PB_SECRET:latest,PB_APP_TOKEN=PB_APP_TOKEN:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest"

# one-off commands against prod (seed, wipes): the job runs the same image with DATABASE_URL
gcloud run jobs update publicbank-seed --region=asia-southeast1 --command=sh --args="-c,<shell string>"
gcloud run jobs execute publicbank-seed --region=asia-southeast1 --wait
gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=publicbank-seed' --limit=30 --format='value(textPayload)' --freshness=10m
```

Gotchas met: `gcloud --args` rejects repeated items (hence `sh -c`); `<` in a placeholder breaks `sh`;
Cloud SQL needs `--edition=ENTERPRISE` for `db-f1-micro`; the compute service account needs
`secretmanager.secretAccessor` + `cloudsql.client`; domain mapping needs the apex verified in Search Console.

## Priorities (as of 26 Oct 2026, six days of the popup left)

1. Get one real agent call through the mini-app: register manifest + URL + app token with Edge City's
   mini-app team (they have a Telegram support group). Expect the manifest to need adjusting to the shipped
   spec; `_member()` in mcp_server.py is where auth assumptions live.
2. Make propose / ballot / rank / approve work entirely inside the Telegram bot (conversation handlers),
   so the web pages are for coordinators only. Keep every core call the same; this is transport work.
3. Run one real round in one residency, through to a real UPI payout, before scaling to four.

Not built: the "twin" (offers → draft proposals), feasibility checks, pool-level round UI, EdgeOS app key.
