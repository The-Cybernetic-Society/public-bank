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
  Federation: a member acts in a pool's round through the child they belong to (`node_in_tree`,
  `require_in_tree`); `cosign_proposal` + `nodes.cosign_min` gate eligibility; `pool_vote='nodes'` means one
  ballot per child by its coordinator (`can_ballot`, `voters`); `ratification_tally`/`close_ratification` pass
  a pool only when every charter-accepted child passes at its own `ratify_pct` (per-child `decisions.detail`);
  `contribute` records a child's money into the parent's round and `spendable_budget` applies `match_pct`
  (contributions + match, capped at the pool's pot); `advance` is the coordinator's one button; `needs`/`add_need`.
- `publicbank/db.py` — schema + SQLite/Postgres shim. `DATABASE_URL` set → Postgres (prod); else SQLite.
  Money is integer paise. All ints are BIGINT (Telegram ids overflow int32). New columns go in `MIGRATIONS`
  (tried on every connect, ignored if present) as well as in `SCHEMA`.
  Every transport holds ONE module-level connection for the life of the instance; Cloud SQL drops idle ones, so
  the Postgres shim uses TCP keepalives and reconnects+retries once on a dropped connection (never mid-`tx()`,
  where it re-raises so the transaction fails whole). Before this, the bot went silent after an idle spell.
- `publicbank/cns_allocate.py` — the allocator (shared with NYU's copy in `edge-city-nyu`; keep in sync,
  don't fork its semantics). Core clamps the budget to Σideal before calling it (rollover note).
- `publicbank/web.py` — FastAPI pages: `/j/<token>` join, `/j/<token>/poster.png`, `/l/<token>` login
  from bot, `/signin` (EdgeOS third-party auth; hidden until `EDGEOS_APP_KEY`), `/me`, `/n/<node>` resident,
  `/c/<node>` coordinator (approve members, advance round → DMs everyone), `/treasury`, `/export/<node>`,
  `/b/<node>` the public live board (no identities; 15 s refresh). `/n/<pool>` shows a child member the pool
  round with lead and co-sign buttons.
  The front door: `/` lists the pools; `/apply/<pool>` is the public registration form (business, person, role,
  WhatsApp number, what/where, team size, one need, what they'd share, would they chip in, research consent). A
  submit makes the business node, an `applications` row and a `join_coord` card; the same phone again updates.
  `/c/<pool>` shows the treasurer every application with a "Send card" link (wa.me to the owner with the card
  prefilled, the manual bridge until the Meta number is live). Opening a card on the web (`/j/<join_coord>`)
  makes the opener coordinator, copies the form's need into `needs` (`core.claim_application`), shows the pool
  charter to accept (`POST /n/<node>/charter`) and a `join_team` link that joins staff at once (a plain `join`
  link still needs approval). Since 30 Oct a submit signs the applicant in at once as coordinator (`verified_by='web'`,
  `core.enter_from_application`) and walks them through `/start/<node>/values` (five TRADE-OFF sliders → `value_vectors`,
  `core.VALUE_DIMENSIONS`: earn more↔spend less, this season↔the long run, my business↔the whole lane, things↔people,
  proven↔new; 1 = left pole, 10 = right, phase before/after: the twin's objective and the outcome baseline) and `/start/<node>/done`
  (the five-rule charter with Agree and continue, team link, the table, Finish → `/start/<node>/finished`, a
  confetti page). Verification is post-hoc: `/c/<pool>` shows new/joined/verified with a Verify button.
  Pages share one look (`CSS`, `FONTS`: Newsreader, Silkscreen, Plex Mono; motion off under reduced-motion).
- `publicbank/convo.py` — pure helpers both chat transports share: rupee/points parsers, rule names, error
  texts, the ballot prompt, receipts. Add shared wording here, not in a bot.
- `publicbank/whatsapp.py` — WhatsApp (Meta Cloud API) for nodes whose members live on WhatsApp (the Goan
  business federation). Identity = the phone number WhatsApp verified (`verified_by='whatsapp'`). `JOIN <code>`
  from an owner's card (`join_coord` token → coordinator, shown the parent's charter, `nodes.charter_at`) or a
  forwarded team link (`join` token → active member at once); `JOIN` alone registers a new node under
  `PB_WA_POOL`. Round as keyword conversations (STATUS/PROPOSE/VOTE/RANK/APPROVE/LINK/PAYOUTS/CANCEL), state in
  `wa_state`, buttons and list messages carry what Telegram callbacks would. `sender` is swappable (tests).
  Free replies only inside the 24h window; otherwise the `PB_WA_TEMPLATE` template. Webhook `/wa` in main.py
  (GET handshake, POST signed with `PB_WA_APP_SECRET`), de-duplicated by message id via `idempotency`.
- `publicbank/setup_businesses.py` — seed a business federation: pool + one node per business + owner cards.
- `publicbank/twin.py` — needs → draft proposals in the pool's round. Plain stem-overlap grouping by default; with
  `ANTHROPIC_API_KEY` the model (`claude-opus-5-5`, server-side fallback on refusal) groups and prices, falling back
  to the plain path. Only need texts and business names go out. Drafts have no lead and no signatures.
  `tags_for` places a text on each axis (-1..+1, left/right stem lists in `TAG_WORDS`, `_conf` says which axes it spoke to);
  `rank_for(con, node_id, proposals)` orders proposals for one business by closeness to its slider positions plus its need (`suggested` = built from or like its need). The
  onboarding table card and the WhatsApp COSIGN list show the top ten in that order.
  `twin.schedule(pool_id)` drafts in a background thread with its own connection (inline under `PB_TWIN_INLINE`); it runs
  after every charter acceptance (web and WhatsApp) and every recorded need, and from the "Draft proposals from needs"
  button on `/c/<pool>`. Nothing else triggers the twin.
- `publicbank/demo.py` — a six-business rehearsal walked to `--stage`; wipes and rebuilds its pool id.
- `publicbank/bot.py` — Telegram: `/start j_<token>` join, `/start l_<token>` link, `/me`, `/status`,
  `/payouts` (treasurer: upi:// button + "Mark paid" → reference), and the round as conversations:
  `/propose`, `/ballot`, `/rank`, `/approve`, `/cancel`. Conversation state is a row in `bot_state`
  (not PTB's in-memory ConversationHandler) because Cloud Run may send the next update to another
  instance. Residents are identified by numeric Telegram id only.
- `publicbank/mcp_server.py` — Agent Village mini-app: 6 tools `publicbank_*`. Auth = `Authorization:
  Bearer PB_APP_TOKEN` + 32-hex `X-Village-Subject`; replays by `Idempotency-Key`. Fails closed.
- `publicbank/main.py` — one ASGI app: `/mcp` → MCP, `/tg/<secret>` → Telegram webhook, rest → web.
  Webhook is registered at startup from `PB_BASE_URL`. `PB_BOT_POLLING=1` for local dev.
- `publicbank/setup_village.py` — seed: pool + residencies + rounds + treasurer + join links.
- `manifest.json`, `skills/publicbank/SKILL.md` — mini-app manifest v2 and the agent skill (sha256 pinned
  in the manifest; re-pin if SKILL.md changes).
- `tests/test_{db,round,web,bot,webhook,whatsapp,pool,apply}.py` — plain scripts, `python tests/test_x.py`; each prints `… OK`.
  Run all eight before any deploy. They also run against Postgres with `DATABASE_URL` set (`test_db` stubs psycopg
  to exercise the shim's reconnect).

## Hard rules (do not relax)

- Never read the Edge City directory or any resident data the resident did not put here themselves.
  Edge City's team said so explicitly (consent terms). A resident's EdgeOS token is used once, for
  `/humans/me`, and dropped.
- The MCP side never reads an agent's memory/context. Every write is confirmed by the human (see SKILL.md).
- Exports carry no names, Telegram ids, EdgeOS ids, UPI ids or phone numbers; `applications` never leaves `/c/<pool>`.
- Approval counts eligible members, not voters (`eligible_members`: active, never a treasurer). Points sum to 100. Amounts are paise.
- A pool's `pool_ratify`: `every` (each child must pass) or `majority` (more than half of the children that voted pass;
  silence is neither). Children made by `apply`/the seeds use `ratify_pct=51`. Only verified businesses count
  (`affected_children` drops children whose application is not `verified`): they can't ballot or decide until the
  treasurer presses Verify. `freeze_proposals` refuses more than `MAX_PROPOSALS` (16, the allocator's exact limit).
- The fund pays vendors directly: a proposal carries `payee_upi`/`payee_name`; the payout is `vendor` when set, else
  the lead's UPI as a reimbursement. `db.tx` rolls back on BaseException.
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
# deploy: `sh node/deploy.sh` (the same command, with --source as the absolute path to node/)
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

Deployed 30 Oct 2026: revision publicbank-00015 (federation + WhatsApp adapter + /pay hand-off + reconnecting shim +
the /apply front door in the site's look, /privacy, /apply/goa alias, country selector, matching off, onboarding flow, trade-off sliders, majority ratification, verified-only voting, vendor payees; `PB_WA_*` and `ANTHROPIC_API_KEY` not yet set on the service). The `publicbank-seed` job currently runs
`python -m publicbank.demo --stage paid --pool goa-biz-demo --treasurer-telegram <id>`; the demo pool is live
at https://bank.cyberneticsutra.com/b/goa-biz-demo. The real pool `goa-business-commons` (Goa Business Commons, no match: the pot is the pot, one business one vote, two co-signers;
`pool_ratify='majority'` set on prod 30 Oct via the seed job)
exists on prod since 29 Oct; businesses register at https://bank.cyberneticsutra.com/apply/goa (`PB_ALIASES`, default `goa=goa-business-commons`).
Its first round (r_03402c8a498f, pot ₹5,00,000, closes early Dec) opened 29 Oct at the proposals stage. It was made with
`python -m publicbank.setup_businesses --pool goa-business-commons --name "Goa Business Commons" --treasurer-telegram <id>`
(no `--business`: businesses register themselves at `/apply/goa-business-commons`).

Gotchas met: pass `--source` as the ABSOLUTE path to `node/` (a `cd node && gcloud run deploy --source .` once
uploaded the repo root, found no Dockerfile, and Buildpacks failed); `gcloud --args` rejects repeated items (hence `sh -c`); `<` in a placeholder breaks `sh`;
Cloud SQL needs `--edition=ENTERPRISE` for `db-f1-micro`; the compute service account needs
`secretmanager.secretAccessor` + `cloudsql.client`; domain mapping needs the apex verified in Search Console.

## Direction (as of 29 Oct 2026)

The Edge City residencies are off the critical path. The deployment is a federation of ~40 Goan businesses
from Ground Floor's programme, each business a node (owner = coordinator, staff = members, one node one
vote in the pool), joined on WhatsApp, run in November through GCCI sessions. The pool is the treasurer's pot (₹5L);
matching (`match_pct`) exists in core and the demo but is OFF for the real pool since 30 Oct: the user never
described matching to the businesses. The form still asks whether a business would chip in; business money never
enters the treasurer's account (each pays its share to the vendor). Strategy, contacts and open questions:
`../../NOTES/edge-city-handoff-2026-10-28.md` (outside git). Built 29 Oct: WhatsApp adapter, pool round across
nodes (co-sign, one vote per node, per-child ratification, contributions + match), needs + twin, live board,
coordinator NEXT in both bots, demo seed. Next: Meta business number + templates (human setup), deploy, seed the
real businesses from Ground Floor's list, a guard at freeze for minimums above the pot, the Postgres run of the suites.

## Priorities (as of 26 Oct 2026, six days of the popup left; superseded above, kept for context)

1. Get one real agent call through the mini-app: register manifest + URL + app token with Edge City's
   mini-app team (they have a Telegram support group). Expect the manifest to need adjusting to the shipped
   spec; `_member()` in mcp_server.py is where auth assumptions live.
2. ~~Propose / ballot / rank / approve inside the Telegram bot~~ — done 26 Oct. Coordinator steps
   (approve members, advance the round) are still on `/c/<node>`.
3. Run one real round in one residency, through to a real UPI payout, before scaling to four.

Not built: the "twin" (offers → draft proposals), feasibility checks, pool-level round UI, EdgeOS app key.
