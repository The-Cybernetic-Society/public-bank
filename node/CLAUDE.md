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
  to the plain path. Only need texts, business names and each business's slider lean go out. Drafts have no lead and no
  signatures. One need is enough for a draft (`min_nodes=1`, written as the lane's shared version); a later need that reads
  like an existing twin draft is attached to it (`core.attach_need_to_draft`) rather than drafted again.
  `tags_for` places a text on each axis (-1..+1, left/right stem lists in `TAG_WORDS`, `_conf` says which axes it spoke to);
  `rank_for(con, node_id, proposals)` orders proposals for one business by closeness to its slider positions plus its need (`suggested` = built from or like its need). The
  onboarding table card and the WhatsApp COSIGN list show the top ten in that order.
  `twin.schedule(pool_id)` drafts in a background thread with its own connection (inline under `PB_TWIN_INLINE`); it runs
  after every charter acceptance (web and WhatsApp) and every recorded need, and from the "Draft proposals from needs"
  button on `/c/<pool>`. The onboarding page itself asks `POST /start/<node>/draft` while its need is open: Cloud Run
  gives a background thread no CPU after the response, so the request is where drafting actually happens on prod.
- `publicbank/schemes.py` + `publicbank/places.py` — the digital twin of Goa's local bodies (added 2 Nov). `schemes.py` imports
  the "Goa schemes — first pass" workbook (`../../NOTES/digital-twin/Goa_schemes_first_pass.xlsx`, outside git; machine-read
  from goa.gov.in PDFs + the GIPARD handbook + the 2026-27 Demands for Grants, unverified by any department) into
  `data/goa_schemes.json` (`python -m publicbank.schemes --import <xlsx>`): one entry per scheme, amendments hung off the
  scheme they change (`core_name`/`parent_of`), `REQUIREMENTS` = regexes that tag what a scheme asks of a place (`needs`:
  st, sc, tribal_area, fishers, brackish, farmers, livestock, rural/urban, bpl, shg, coop, fra, ngo …; `bodies` = which local
  body applies; `people` = applicant classes; `scope`). Since 2 Nov the import also merges the business portals
  (`--business msme.goa.gov.in=data/sources/goa_msme_schemes.csv --business startup.goa.gov.in=…`, `merge_business`: same
  name or core name → the workbook scheme gains the portal as a source, link and blanks; else a new scheme with `tags`)
  and Goa Online (`--services data/sources/goa_online_services.csv`, `merge_services`: `cat["services"]` plus `apply_online`
  on the scheme a service applies for). 770 schemes, 212 services; data/sources/README.md says what each portal gave and
  withheld (Startup Goa was read from the Internet Archive: its certificate has expired). `places.py` builds `data/goa_divisions.json` from `data/sources/`
  (`--build`): Census 2011 PCA village rows summed per village panchayat through the LGD village→local-body mapping (189
  panchayats, 14 towns; six split villages halved), plus optional `goa_place_names.csv`, `goa_place_flags.csv` (coastal,
  estuarine) and `goa_programmes.csv` (mgnrega_job_cards, shgs, fisher_households, dairy_societies, pmayg_houses,
  weaker_panchayat) merged by LGD code or id. Talukas, districts and the state are roll-ups computed on request.
  `verdict(place, scheme)` → eligible | plausible | unlikely (+ `drawing` when programme data shows it running), with the
  reasons and the step for plausible; `judge(key, profile)` is the one place a rule meets a figure. Group requirements
  (shg/coop/ngo/fra) only constrain when nobody can apply alone (`INDIVIDUAL_CLASSES`); a body scheme's scope is which body
  applies, not its text. Pages: `/twin?level=` index, `/twin/<id>` (facts card, counts, sections body/groups/people, unlikely
  folded, budget lines), `/twin/<id>.json`. `tests/test_twin.py`. Data is static for the process (`summary_of` cache).
  `publicbank/twinmap.py` — the tappable map on the front page and `/twin`: `data/goa_map.json` (DataMeet census village
  polygons, ODbL, dissolved per body/taluka/district by `places --build-map`, shapely at build time only; three bodies have
  no shape) served at `/twin/map.json`; plain SVG + JS, three scales on one control, tap a district or taluka → card + "See
  its …" zooms in with a breadcrumb back out; state in the URL hash (`#place=…&level=…&focus=…`) so the front page's 60 s
  refresh and shared links land on the same place. The card (`/twin/<id>/card.json`, `places.card`) shows four figures, the
  counts and the three schemes worth attention first: `places.score` = impact (money on a log scale × who can act × reach
  here × how much more here than the typical body, `across_goa` rarity/avg_reach cached per process) × ease (eligible 1,
  plausible 0.55 − 0.1/step, unlikely or already running 0); `top_schemes` dedupes near-identical titles, and the card shows
  `top_by_audience`: one scheme per slot (the body itself, groups and societies, households and people), so a place reads as a
  portrait rather than the same big-money body grant everywhere.
  Since 2 Nov the registration form asks where the shop is: `applications.address` (as typed; stays on `/c/<pool>` and in its
  CSV, never public) and `applications.place_id` → `nodes.place_id` (the local body, `places.resolve`: a typed name, "Name ·
  Taluka", a village or an alias; else the server reads a place name off the address). The form offers a typeahead over
  `places.choices()` (539 labels: bodies, census villages, aliases, inline as a datalist), a "Looks like X — use it" suggestion
  from the address, and "Use my location" → `GET /twin/locate?lat&lon` (`places.locate`, ray casting over the map polygons; the
  point is not stored). `GET /twin/businesses.json` lists the home pool's VERIFIED businesses with their place (`core.verified_
  businesses`); the map draws a count marker per place (`c`, each feature's representative point) and the card lists the names.
  WhatsApp self-registration (`JOIN` with no code) and the seeds do not set a place yet.
  Front page order (2 Nov): intro, fund bar + Register, Who's in as seals (`_seals`), `commonsmap.map_section` (dots and the
  most-backed proposal's connections, no controls), Funding proposals, `commonsmap.twin_section` (three paragraphs on the twin,
  the AI drafting and the simulation, then the two sliders as a live demo whose best-fit draft also drives the map's lines),
  How it works, money, blog, record. The sliders are a demonstration, not a control: untouched, the map shows the most-backed
  proposal; touched, it follows the lean (`touched`, kept in the hash).
  `publicbank/commonsmap.py` — the front page's map IS the Commons (2 Nov, replacing the place-twin section there): every
  business of the home pool as a dot in its place (`businesses`: node or application `place_id` → the map file's `c`,
  fanned in a ring when several share a place; dashed = not yet verified), the talukas faint behind, two of the five
  sliders (earn, risk) above; the best-fit proposal for that lean (`twin.tags_for` + the owners' fit rule, client-side)
  draws lines to the businesses it connects (backers and lead filled, built-from dashed: the twin quotes "- Name: need"
  lines) with a hub count; tapping a business shows its need and its proposals. `/commons/<pool>/map.json`; state in the
  hash (`#commons=earn=5,risk=5&biz=`). The owners' own slider answers are never sent. The place twin stays at `/twin`,
  linked from "For the record".
  Scheme-backed proposals: `schemes.CO_FUNDING` is a short hand-kept table (pattern → scheme core name → what it pays);
  `schemes.co_funding(title, text)` resolves it against the catalogue and `core.add_proposal`/`add_twin_draft` store the
  result in `proposals.scheme_json`; the front page row, the board, the table card and the map card show "a scheme could
  carry part of this". `python -m publicbank.schemes --attach-cofunding` backfills open proposals.
  `publicbank/businesses.py` — the BUSINESS twin (2 Nov): a registered firm read against the schemes a firm can draw
  (`candidates`: applicants include Business / entrepreneur, or a portal tag). `schemes.business_tag` (run inside `tag`)
  gives each scheme `biz` = needs (udyam, startup, gst, women_only, sc_st_only, new_unit, young, small, local; `BUSINESS_RULES`),
  sectors (`SECTOR_RULES`) and offer (money | credit | support). `profile(application)` reads the trade off the form's words
  (`TRADE_WORDS`), team size, and the three optional answers the form asks since 2 Nov (`applications.registrations`
  udyam|startup|gst|none and `age_band` new|young|established; the form asks nothing about who the owner is, so `owner_tags`
  stays empty and schemes reserved for women or SC/ST owners stay plausible; never public). The map card and the twin index headline households, people,
  farming and coast; the census SC/ST shares sit last on the twin page, used by the rules, not a headline. `verdict` → eligible |
  plausible (missing or unknown entry ticket, with the step) | unlikely (trade, owner category, size, age mismatch).
  `card(con, node_id)`: counts, one scheme per slot (money, credit, support), the steps that unlock the most, the full list.
  Shown to the owner only: `/n/<node>/schemes` and a card on `/start/<node>/done`. `place_draw` gives the map card counts for
  a place's verified businesses ("eligible for N schemes between them, the step that opens the most"), never who holds what.
  Startup: `main.py`'s lifespan calls `places.warm()` (every body's and roll-up's `statuses_of`, the `across_goa` table, the map
  file; under a second) so the first visitor never pays the compute; `PB_TWIN_WARM=0` skips it. The map section preloads
  `/twin/map.json?v=` and `/twin/businesses.json` from where it sits in the page, draws the coarse state `outline` (in the
  map file, built with the map) with a stroke animation and a wandering gold dot until the data lands, then the real shapes
  fade in; the card counts up the scheme total while its JSON loads. Both stop under prefers-reduced-motion.
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
- `tests/test_{db,round,web,bot,webhook,whatsapp,pool,apply,twin}.py` — plain scripts, `python tests/test_x.py`; each prints `… OK`.
  Run all nine before any deploy. They also run against Postgres with `DATABASE_URL` set (`test_db` stubs psycopg
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

Live at https://goa.cyberneticsutra.com since 2 Nov 2026 (bank.cyberneticsutra.com forwards there through `PB_OLD_HOSTS`; both are Cloud Run domain mappings) (Cloud Run `publicbank`, Cloud SQL Postgres `publicbank`,
secrets in Secret Manager: `DATABASE_URL`, `PB_SECRET`, `PB_APP_TOKEN`, `TELEGRAM_BOT_TOKEN`).
Bot: `@cybernetic_society_bot`. Treasurer Telegram id is in the seed command history.

```bash
# deploy: `sh node/deploy.sh` (the same command, with --source as the absolute path to node/)
CONN=$(gcloud sql instances describe publicbank --format='value(connectionName)')
gcloud run deploy publicbank --source . --region=asia-southeast1 --allow-unauthenticated \
  --add-cloudsql-instances="$CONN" --min-instances=1 --max-instances=3 --memory=512Mi \
  --set-env-vars="PB_BASE_URL=https://goa.cyberneticsutra.com,PB_OLD_HOSTS=bank.cyberneticsutra.com,PB_BOT_USERNAME=cybernetic_society_bot,EDGEOS_API_BASE=https://api.edgeos.world" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,PB_SECRET=PB_SECRET:latest,PB_APP_TOKEN=PB_APP_TOKEN:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest"

# one-off commands against prod (seed, wipes): the job runs the same image with DATABASE_URL
gcloud run jobs update publicbank-seed --region=asia-southeast1 --command=sh --args="-c,<shell string>"
gcloud run jobs execute publicbank-seed --region=asia-southeast1 --wait
gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=publicbank-seed' --limit=30 --format='value(textPayload)' --freshness=10m
```

Deployed 2 Nov 2026: revision publicbank-00032 (federation + WhatsApp adapter + /pay hand-off + reconnecting shim +
the /apply front door in the site's look, /privacy, /apply/goa alias, country selector, matching off, onboarding flow, trade-off sliders, majority ratification, verified-only voting, vendor payees,
the front page as the Commons: fund bar and button, Who's in as seals, the Commons map on the most-backed proposal, the board (what businesses put forward or lead, plus one unled AI-twin draft) opening on a tap, "What is an AI digital twin?" with the two sliders as a demo, "AI twin" pills; registration coarse to fine (place, pin, lane); `places --set-place`; the place twin at /twin (warmed at startup); the business portals in the catalogue, the business twin on the owner's pages, co-funding schemes on new proposals; the shop's address/place on registration; `PB_WA_*` and `ANTHROPIC_API_KEY` not yet set on the service). The four proposals already on prod predate co-funding: `python -m publicbank.schemes --attach-cofunding` through the seed job would backfill them. The `publicbank-seed` job currently runs
`python -m publicbank.demo --stage paid --pool goa-biz-demo --treasurer-telegram <id>`; the demo pool is live
at https://goa.cyberneticsutra.com/b/goa-biz-demo. The real pool `goa-business-commons` (Goa Business Commons, no match: the pot is the pot, one business one vote, two co-signers;
`pool_ratify='majority'` set on prod 30 Oct via the seed job)
exists on prod since 29 Oct; businesses register at https://goa.cyberneticsutra.com/apply/goa (`PB_ALIASES`, default `goa=goa-business-commons`).
Its first round (r_03402c8a498f, pot ₹5,00,000, closes early Dec) opened 29 Oct at the proposals stage. It was made with
`python -m publicbank.setup_businesses --pool goa-business-commons --name "Goa Business Commons" --treasurer-telegram <id>`
(no `--business`: businesses register themselves at `/apply/goa-business-commons`).

Gotchas met: the `publicbank-seed` job keeps whatever image it was last given, NOT the service's: after a deploy, point it at the
service's image (`gcloud run jobs update publicbank-seed --region=asia-southeast1 --image "$(gcloud run services describe publicbank
--region=asia-southeast1 --format='value(spec.template.spec.containers[0].image)')"`) before running new commands through it, or
"No module named …" follows; `--args` splits on commas, so a command with commas needs the `^|^` delimiter (`--args='^|^-c|<cmd>'`);
executing the job re-runs its LAST command, so update first. Pass `--source` as the ABSOLUTE path to `node/` (a `cd node && gcloud run deploy --source .` once
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
