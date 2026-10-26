"""
SQLite store for a Public Bank node service.

One service can host many nodes (a residency, a village pool, a standing node).
Every table that holds a person references `members`, which records HOW the
person was verified (`verified_by`), so the identity layer can change under a
node without touching anything else.

Money is integer paise (₹ × 100). Amount fields never hold floats.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

DB_PATH = os.environ.get("PB_DB", os.path.join(os.path.dirname(__file__), "..", "publicbank.sqlite"))

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS nodes (
  id            TEXT PRIMARY KEY,            -- slug, e.g. 'goa-2026/residency-x'
  name          TEXT NOT NULL,
  kind          TEXT NOT NULL DEFAULT 'group',   -- group | pool | standing
  parent_id     TEXT REFERENCES nodes(id),   -- the super-node this node contributes to, if any
  levy_pct      INTEGER NOT NULL DEFAULT 0,  -- % of pot sent up to parent each round
  ratify_rule   TEXT NOT NULL DEFAULT 'approval_60',
  ratify_pct    INTEGER NOT NULL DEFAULT 60,
  allocation_rule TEXT NOT NULL DEFAULT 'mes',
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rounds (
  id            TEXT PRIMARY KEY,
  node_id       TEXT NOT NULL REFERENCES nodes(id),
  budget_paise  INTEGER NOT NULL,
  status        TEXT NOT NULL DEFAULT 'proposals',  -- proposals | ballots | plans | ratifying | ratified | failed | paid | closed
  opens_at      INTEGER NOT NULL,
  proposals_close_at INTEGER,
  ballots_close_at   INTEGER,
  closes_at     INTEGER,
  cap_at_ideal  INTEGER NOT NULL DEFAULT 1,
  winner_plan_id TEXT,
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
  id            TEXT PRIMARY KEY,            -- internal person id (uuid)
  display       TEXT,                        -- a first name or handle the person chose to show; may be NULL
  telegram_id   INTEGER UNIQUE,              -- numeric Telegram user id, if linked
  edgeos_human_id TEXT UNIQUE,               -- if signed in with Edge City
  village_subject TEXT UNIQUE,               -- X-Village-Subject from the mini-app dispatcher, if used
  upi_id        TEXT,                        -- only for leads who receive payouts; never shown publicly
  verified_by   TEXT NOT NULL,               -- deeplink | coordinator | edgeos_login | miniapp | world_id
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
  node_id       TEXT NOT NULL REFERENCES nodes(id),
  member_id     TEXT NOT NULL REFERENCES members(id),
  role          TEXT NOT NULL DEFAULT 'member',   -- member | coordinator | treasurer
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending | active | removed
  approved_by   TEXT REFERENCES members(id),
  joined_at     INTEGER NOT NULL,
  PRIMARY KEY (node_id, member_id)
);

CREATE TABLE IF NOT EXISTS proposals (
  id            TEXT PRIMARY KEY,
  round_id      TEXT NOT NULL REFERENCES rounds(id),
  title         TEXT NOT NULL,
  text          TEXT NOT NULL,
  min_paise     INTEGER NOT NULL DEFAULT 0,
  ideal_paise   INTEGER NOT NULL,
  lead_id       TEXT REFERENCES members(id),     -- must be set before the list freezes
  source        TEXT NOT NULL DEFAULT 'human',   -- human | agent | twin
  cosign_nodes  TEXT NOT NULL DEFAULT '[]',      -- JSON list of node ids (super-node proposals)
  status        TEXT NOT NULL DEFAULT 'draft',   -- draft | eligible | withdrawn | merged
  created_by    TEXT REFERENCES members(id),
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS offers (
  id            TEXT PRIMARY KEY,
  node_id       TEXT NOT NULL REFERENCES nodes(id),
  member_id     TEXT NOT NULL REFERENCES members(id),
  what          TEXT NOT NULL,
  quantity      INTEGER NOT NULL DEFAULT 1,
  unit          TEXT NOT NULL DEFAULT 'items',
  scope         TEXT NOT NULL DEFAULT 'node',    -- node | network | public
  status        TEXT NOT NULL DEFAULT 'offered', -- offered | confirmed | used | withdrawn
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ballots (
  round_id      TEXT NOT NULL REFERENCES rounds(id),
  member_id     TEXT NOT NULL REFERENCES members(id),
  points        TEXT NOT NULL,                   -- JSON {proposal_id: int}, sums to 100 (unspent allowed under key "_unspent")
  revision      INTEGER NOT NULL DEFAULT 1,
  submitted_at  INTEGER NOT NULL,
  PRIMARY KEY (round_id, member_id)
);

CREATE TABLE IF NOT EXISTS plans (
  id            TEXT PRIMARY KEY,
  round_id      TEXT NOT NULL REFERENCES rounds(id),
  rule          TEXT NOT NULL,                   -- mes | nash | utilitarian | maximin
  amounts       TEXT NOT NULL,                   -- JSON {proposal_id: paise}
  receipts      TEXT NOT NULL DEFAULT '{}',      -- JSON {member_id: {proposal_id: paise}} (mes only)
  valid         INTEGER NOT NULL DEFAULT 1,
  notes         TEXT NOT NULL DEFAULT '[]',
  computed_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rankings (
  round_id      TEXT NOT NULL REFERENCES rounds(id),
  member_id     TEXT NOT NULL REFERENCES members(id),
  ranking       TEXT NOT NULL,                   -- JSON [plan_id, ...] best first
  submitted_at  INTEGER NOT NULL,
  PRIMARY KEY (round_id, member_id)
);

CREATE TABLE IF NOT EXISTS ratifications (
  round_id      TEXT NOT NULL REFERENCES rounds(id),
  plan_id       TEXT NOT NULL REFERENCES plans(id),
  member_id     TEXT NOT NULL REFERENCES members(id),
  verdict       TEXT NOT NULL,                   -- approve | reject
  submitted_at  INTEGER NOT NULL,
  PRIMARY KEY (round_id, member_id)
);

CREATE TABLE IF NOT EXISTS decisions (
  id            TEXT PRIMARY KEY,
  round_id      TEXT NOT NULL REFERENCES rounds(id),
  plan_id       TEXT REFERENCES plans(id),
  verdict       TEXT NOT NULL,                   -- ratified | failed
  rule          TEXT NOT NULL,
  approvals     INTEGER NOT NULL,
  rejections    INTEGER NOT NULL,
  eligible      INTEGER NOT NULL,
  ballot_hash   TEXT NOT NULL,                   -- sha256 over the canonical ballot set
  decided_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payouts (
  id            TEXT PRIMARY KEY,
  round_id      TEXT NOT NULL REFERENCES rounds(id),
  proposal_id   TEXT NOT NULL REFERENCES proposals(id),
  payee_member_id TEXT REFERENCES members(id),   -- the lead (reimbursement) or NULL for a direct vendor payment
  payee_upi     TEXT,                            -- vendor UPI id if paying a vendor directly
  amount_paise  INTEGER NOT NULL,
  kind          TEXT NOT NULL DEFAULT 'reimbursement',  -- reimbursement | vendor
  status        TEXT NOT NULL DEFAULT 'queued',  -- queued | sent | paid | receipt_ok | void
  reference     TEXT,                            -- UPI/IMPS reference once paid
  receipt_note  TEXT,
  created_at    INTEGER NOT NULL,
  paid_at       INTEGER
);

CREATE TABLE IF NOT EXISTS contributions (
  id            TEXT PRIMARY KEY,
  from_node_id  TEXT NOT NULL REFERENCES nodes(id),
  to_node_id    TEXT NOT NULL REFERENCES nodes(id),
  round_id      TEXT REFERENCES rounds(id),
  amount_paise  INTEGER NOT NULL,
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
  token         TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,                   -- join | ballot | ratify | login
  node_id       TEXT,
  round_id      TEXT,
  member_id     TEXT,
  expires_at    INTEGER NOT NULL,
  used_at       INTEGER
);

CREATE TABLE IF NOT EXISTS idempotency (
  key           TEXT PRIMARY KEY,
  response      TEXT NOT NULL,
  created_at    INTEGER NOT NULL
);
"""


def now() -> int:
    return int(time.time())


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or DB_PATH, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


@contextmanager
def tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def rows(rs: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rs]


def j(s: str | None, default: Any) -> Any:
    try:
        return json.loads(s) if s else default
    except json.JSONDecodeError:
        return default
