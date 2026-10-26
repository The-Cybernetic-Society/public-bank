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

## Surfaces

| Surface | For | Where |
|---|---|---|
| Telegram bot | residents (join via QR, get notified), treasurer (payout queue) | `publicbank/bot.py` |
| Web | propose, ballot, rank, approve, coordinator & treasury pages, export | `publicbank/web.py` |
| MCP mini-app | residents' agents in Agent Village (confirmed writes only) | `publicbank/mcp_server.py`, `manifest.json`, `skills/publicbank/SKILL.md` |

Identity is pluggable and recorded per member (`verified_by`): a join link / QR (`deeplink`), a
coordinator's approval (`coordinator`), Edge City third-party sign-in (`edgeos_login`, enabled by
`EDGEOS_APP_KEY`), or the mini-app dispatcher's `X-Village-Subject` (`miniapp`). No directory is ever
read; nothing about a resident enters the system except what they or their coordinator put there.

## Run

```bash
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

Deploy (GCP): `PROJECT=<id> TELEGRAM_BOT_TOKEN=<token> ./deploy-gcp.sh` creates Cloud SQL Postgres, secrets, the Cloud Run service (one warm instance) and the domain mapping. Updates: `gcloud run deploy publicbank --source . --region asia-southeast1`. Railway also works: `railway.json` is included.
variables from `.env.example` → add the custom domain and point `PB_BASE_URL` at it. `railway.json`
has the start command and health check. Seed with `railway run python -m publicbank.setup_village …`.

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
