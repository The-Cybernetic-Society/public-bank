"""
Core operations of a Public Bank node: membership, proposals, ballots, plans,
ratification, payouts, levy, and the five-object export.

Everything here is transport-agnostic. The web pages, the Telegram bot and the
MCP server all call these functions with a `member_id` they have already
verified. No function here trusts a caller-supplied identity.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import cns_allocate as alloc
from .db import j, new_id, now, row, rows, tx

TOKEN_TTL_JOIN = 7 * 24 * 3600
TOKEN_TTL_ACTION = 3 * 24 * 3600


class PBError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


# ----------------------------------------------------------------------------
# nodes and rounds
# ----------------------------------------------------------------------------
def create_node(con, node_id: str, name: str, kind: str = "group", parent_id: str | None = None,
                levy_pct: int = 0, ratify_pct: int = 60) -> dict:
    with tx(con):
        con.execute("INSERT INTO nodes (id,name,kind,parent_id,levy_pct,ratify_pct,created_at) VALUES (?,?,?,?,?,?,?)",
                    (node_id, name, kind, parent_id, levy_pct, ratify_pct, now()))
    return get_node(con, node_id)


def get_node(con, node_id: str) -> dict:
    r = row(con.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone())
    if not r:
        raise PBError("node_not_found")
    return r


def open_round(con, node_id: str, budget_paise: int, proposals_close_at: int | None = None,
               ballots_close_at: int | None = None, closes_at: int | None = None) -> dict:
    node = get_node(con, node_id)
    rid = new_id("r_")
    with tx(con):
        con.execute("""INSERT INTO rounds (id,node_id,budget_paise,status,opens_at,proposals_close_at,ballots_close_at,closes_at,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (rid, node_id, budget_paise, "proposals", now(), proposals_close_at, ballots_close_at, closes_at, now()))
        # levy: a fixed fraction of the pot is contributed upward at open (escrowed for the parent round)
        if node["parent_id"] and node["levy_pct"]:
            levy = budget_paise * node["levy_pct"] // 100
            con.execute("INSERT INTO contributions (id,from_node_id,to_node_id,round_id,amount_paise,created_at) VALUES (?,?,?,?,?,?)",
                        (new_id("c_"), node_id, node["parent_id"], rid, levy, now()))
    return get_round(con, rid)


def get_round(con, round_id: str) -> dict:
    r = row(con.execute("SELECT * FROM rounds WHERE id=?", (round_id,)).fetchone())
    if not r:
        raise PBError("round_not_found")
    return r


def current_round(con, node_id: str) -> dict | None:
    return row(con.execute("SELECT * FROM rounds WHERE node_id=? AND status NOT IN ('closed') ORDER BY created_at DESC LIMIT 1",
                           (node_id,)).fetchone())


def spendable_budget(con, rnd: dict) -> int:
    """Budget minus the levy sent upward for this round."""
    levy = con.execute("SELECT COALESCE(SUM(amount_paise),0) FROM contributions WHERE round_id=?", (rnd["id"],)).fetchone()[0]
    return rnd["budget_paise"] - int(levy)


def set_round_status(con, round_id: str, status: str) -> None:
    with tx(con):
        con.execute("UPDATE rounds SET status=? WHERE id=?", (status, round_id))


# ----------------------------------------------------------------------------
# members and tokens
# ----------------------------------------------------------------------------
def upsert_member(con, *, verified_by: str, telegram_id: int | None = None, edgeos_human_id: str | None = None,
                  village_subject: str | None = None, display: str | None = None) -> dict:
    """Find a member by any linked identity, else create. Links a new identity to an existing member when
    the caller has already established both belong to the same person (e.g. signed in, then tapped the bot)."""
    q, args = [], []
    if telegram_id is not None: q.append("telegram_id=?"); args.append(telegram_id)
    if edgeos_human_id: q.append("edgeos_human_id=?"); args.append(edgeos_human_id)
    if village_subject: q.append("village_subject=?"); args.append(village_subject)
    if not q:
        raise PBError("no_identity")
    r = row(con.execute(f"SELECT * FROM members WHERE {' OR '.join(q)}", args).fetchone())
    with tx(con):
        if r:
            con.execute("""UPDATE members SET telegram_id=COALESCE(?,telegram_id), edgeos_human_id=COALESCE(?,edgeos_human_id),
                           village_subject=COALESCE(?,village_subject), display=COALESCE(?,display) WHERE id=?""",
                        (telegram_id, edgeos_human_id, village_subject, display, r["id"]))
            mid = r["id"]
        else:
            mid = new_id("m_")
            con.execute("INSERT INTO members (id,display,telegram_id,edgeos_human_id,village_subject,verified_by,created_at) VALUES (?,?,?,?,?,?,?)",
                        (mid, display, telegram_id, edgeos_human_id, village_subject, verified_by, now()))
    return row(con.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone())


def join_node(con, node_id: str, member_id: str, role: str = "member", auto_active: bool = False) -> dict:
    get_node(con, node_id)
    with tx(con):
        con.execute("""INSERT INTO memberships (node_id,member_id,role,status,joined_at) VALUES (?,?,?,?,?)
                       ON CONFLICT(node_id,member_id) DO UPDATE SET role=excluded.role""",
                    (node_id, member_id, role, "active" if auto_active else "pending", now()))
    return row(con.execute("SELECT * FROM memberships WHERE node_id=? AND member_id=?", (node_id, member_id)).fetchone())


def approve_member(con, node_id: str, member_id: str, by_member_id: str) -> None:
    if not is_role(con, node_id, by_member_id, ("coordinator", "treasurer")):
        raise PBError("not_coordinator")
    with tx(con):
        con.execute("UPDATE memberships SET status='active', approved_by=? WHERE node_id=? AND member_id=?",
                    (by_member_id, node_id, member_id))


def is_role(con, node_id: str, member_id: str, roles: tuple[str, ...]) -> bool:
    r = con.execute("SELECT role,status FROM memberships WHERE node_id=? AND member_id=?", (node_id, member_id)).fetchone()
    return bool(r and r["status"] == "active" and r["role"] in roles)


def require_active(con, node_id: str, member_id: str) -> None:
    r = con.execute("SELECT status FROM memberships WHERE node_id=? AND member_id=?", (node_id, member_id)).fetchone()
    if not r or r["status"] != "active":
        raise PBError("not_a_member")


def active_members(con, node_id: str) -> list[dict]:
    return rows(con.execute("SELECT m.* FROM members m JOIN memberships ms ON ms.member_id=m.id WHERE ms.node_id=? AND ms.status='active'",
                            (node_id,)).fetchall())


def mint_token(con, kind: str, *, node_id: str | None = None, round_id: str | None = None, member_id: str | None = None,
               ttl: int = TOKEN_TTL_ACTION) -> str:
    t = secrets.token_urlsafe(24)
    with tx(con):
        con.execute("INSERT INTO tokens (token,kind,node_id,round_id,member_id,expires_at) VALUES (?,?,?,?,?,?)",
                    (t, kind, node_id, round_id, member_id, now() + ttl))
    return t


def read_token(con, token: str, kind: str, consume: bool = False) -> dict:
    r = row(con.execute("SELECT * FROM tokens WHERE token=? AND kind=?", (token, kind)).fetchone())
    if not r or r["expires_at"] < now() or (consume and r["used_at"]):
        raise PBError("bad_token")
    if consume:
        with tx(con):
            con.execute("UPDATE tokens SET used_at=? WHERE token=?", (now(), token))
    return r


# ----------------------------------------------------------------------------
# proposals and offers
# ----------------------------------------------------------------------------
def add_proposal(con, round_id: str, member_id: str, *, title: str, text: str, ideal_paise: int, min_paise: int = 0,
                 lead_id: str | None = None, source: str = "human", cosign_nodes: list[str] | None = None) -> dict:
    rnd = get_round(con, round_id)
    if rnd["status"] != "proposals":
        raise PBError("proposals_closed")
    require_active(con, rnd["node_id"], member_id)
    if ideal_paise <= 0 or min_paise < 0 or min_paise > ideal_paise:
        raise PBError("bad_amounts", "ideal must be > 0 and minimum ≤ ideal")
    pid = new_id("p_")
    status = "eligible" if lead_id else "draft"
    with tx(con):
        con.execute("""INSERT INTO proposals (id,round_id,title,text,min_paise,ideal_paise,lead_id,source,cosign_nodes,status,created_by,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, round_id, title.strip()[:120], text.strip()[:2000], min_paise, ideal_paise, lead_id, source,
                     json.dumps(cosign_nodes or []), status, member_id, now()))
    return get_proposal(con, pid)


def get_proposal(con, pid: str) -> dict:
    r = row(con.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone())
    if not r:
        raise PBError("proposal_not_found")
    return r


def lead_proposal(con, pid: str, member_id: str) -> dict:
    """A member agrees to lead; this is what makes an agent- or twin-suggested proposal eligible."""
    p = get_proposal(con, pid)
    rnd = get_round(con, p["round_id"])
    require_active(con, rnd["node_id"], member_id)
    with tx(con):
        con.execute("UPDATE proposals SET lead_id=?, status='eligible' WHERE id=? AND status IN ('draft','eligible')", (member_id, pid))
    return get_proposal(con, pid)


def withdraw_proposal(con, pid: str, member_id: str) -> None:
    p = get_proposal(con, pid)
    if member_id not in (p["created_by"], p["lead_id"]):
        raise PBError("not_yours")
    with tx(con):
        con.execute("UPDATE proposals SET status='withdrawn' WHERE id=?", (pid,))


def eligible_proposals(con, round_id: str) -> list[dict]:
    return rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND status='eligible' ORDER BY created_at", (round_id,)).fetchall())


def freeze_proposals(con, round_id: str) -> list[dict]:
    """Close the proposal stage. Drafts without a lead are left behind (never eligible)."""
    rnd = get_round(con, round_id)
    if rnd["status"] != "proposals":
        raise PBError("wrong_stage")
    props = eligible_proposals(con, round_id)
    if not props:
        raise PBError("no_proposals")
    set_round_status(con, round_id, "ballots")
    return props


def add_offer(con, node_id: str, member_id: str, *, what: str, quantity: int = 1, unit: str = "items", scope: str = "node") -> dict:
    require_active(con, node_id, member_id)
    oid = new_id("o_")
    with tx(con):
        con.execute("INSERT INTO offers (id,node_id,member_id,what,quantity,unit,scope,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (oid, node_id, member_id, what.strip()[:200], max(1, int(quantity)), unit.strip()[:32], scope, now()))
    return row(con.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone())


def confirm_offer(con, offer_id: str, member_id: str) -> None:
    with tx(con):
        n = con.execute("UPDATE offers SET status='confirmed' WHERE id=? AND member_id=?", (offer_id, member_id)).rowcount
    if not n:
        raise PBError("not_yours")


# ----------------------------------------------------------------------------
# ballots
# ----------------------------------------------------------------------------
def submit_ballot(con, round_id: str, member_id: str, points: dict[str, int]) -> dict:
    rnd = get_round(con, round_id)
    if rnd["status"] != "ballots":
        raise PBError("ballots_closed")
    require_active(con, rnd["node_id"], member_id)
    valid_ids = {p["id"] for p in eligible_proposals(con, round_id)} | {"_unspent"}
    clean: dict[str, int] = {}
    for k, v in points.items():
        if k not in valid_ids:
            raise PBError("unknown_proposal", k)
        iv = int(v)
        if iv < 0:
            raise PBError("negative_points")
        if iv:
            clean[k] = iv
    if sum(clean.values()) != 100:
        raise PBError("points_must_sum_100", str(sum(clean.values())))
    with tx(con):
        con.execute("""INSERT INTO ballots (round_id,member_id,points,revision,submitted_at) VALUES (?,?,?,1,?)
                       ON CONFLICT(round_id,member_id) DO UPDATE SET points=excluded.points, revision=ballots.revision+1, submitted_at=excluded.submitted_at""",
                    (round_id, member_id, json.dumps(clean, sort_keys=True), now()))
    return row(con.execute("SELECT * FROM ballots WHERE round_id=? AND member_id=?", (round_id, member_id)).fetchone())


def ballots_of(con, round_id: str) -> list[dict]:
    return rows(con.execute("SELECT * FROM ballots WHERE round_id=?", (round_id,)).fetchall())


def ballot_hash(con, round_id: str) -> str:
    """sha256 over the canonical ballot set: sorted (member, points-json) pairs. Lets a third party verify a
    published tally without seeing who voted what: we publish the hash, a node can reveal on request."""
    items = sorted((b["member_id"], b["points"]) for b in ballots_of(con, round_id))
    return hashlib.sha256(json.dumps(items, separators=(",", ":")).encode()).hexdigest()


# ----------------------------------------------------------------------------
# plans
# ----------------------------------------------------------------------------
def compute_plans(con, round_id: str, rules: tuple[str, ...] = ("mes", "nash", "utilitarian", "maximin")) -> list[dict]:
    """Turn 100-point ballots into the support matrix the allocator expects and compute one plan per rule."""
    rnd = get_round(con, round_id)
    if rnd["status"] != "ballots":
        raise PBError("wrong_stage")
    props = eligible_proposals(con, round_id)
    ballots = ballots_of(con, round_id)
    if not ballots:
        raise PBError("no_ballots")
    budget = spendable_budget(con, rnd)
    # Allocator works in whole units; use rupees for conditioning, convert back to paise.
    P = [alloc.Proposal(p["id"], p["title"], p["min_paise"] / 100, p["ideal_paise"] / 100) for p in props]
    people = [b["member_id"] for b in ballots]
    S = np.zeros((len(ballots), len(P)))
    idx = {p.id: k for k, p in enumerate(P)}
    for i, b in enumerate(ballots):
        for pid, pts in j(b["points"], {}).items():
            if pid in idx:
                S[i, idx[pid]] = pts
    cap = bool(rnd["cap_at_ideal"])
    # With ideals capped, a pot can never spend more than the sum of ideals. Anything above that is
    # not a failure, it is money that stays in the pot (rollover). Allocate against the spendable part.
    rollover = 0
    if cap:
        max_spend = sum(p["ideal_paise"] for p in props)
        if max_spend < budget:
            rollover, budget = budget - max_spend, max_spend
    out = []
    with tx(con):
        con.execute("DELETE FROM plans WHERE round_id=?", (round_id,))
        for rule in rules:
            plan = alloc.mes(P, people, S, budget / 100, cap) if rule == "mes" else alloc.optimise(rule, P, people, S, budget / 100, cap)
            amounts = {k: int(round(v * 100)) for k, v in plan.amounts.items()}
            receipts = {m: {k: int(round(v * 100)) for k, v in rec.items()} for m, rec in plan.receipts.items()}
            errs = alloc.check_plan(plan.amounts, P, budget / 100, cap)
            if rollover:
                plan.notes.append(f"₹{rollover // 100:,} stays in the pot: proposals' ideals add up to less than the budget.")
            plid = new_id("pl_")
            con.execute("INSERT INTO plans (id,round_id,rule,amounts,receipts,valid,notes,computed_at) VALUES (?,?,?,?,?,?,?,?)",
                        (plid, round_id, rule, json.dumps(amounts), json.dumps(receipts), 0 if errs else 1, json.dumps(plan.notes + errs), now()))
            out.append(plid)
        con.execute("UPDATE rounds SET status='plans' WHERE id=?", (round_id,))
    return rows(con.execute("SELECT * FROM plans WHERE round_id=? ORDER BY computed_at", (round_id,)).fetchall())


def plans_of(con, round_id: str) -> list[dict]:
    return rows(con.execute("SELECT * FROM plans WHERE round_id=?", (round_id,)).fetchall())


def receipt_for(con, round_id: str, member_id: str, plan_id: str | None = None) -> dict[str, int]:
    """Where this member's equal share went, in paise, under the MES plan (or a named plan)."""
    q = "SELECT receipts FROM plans WHERE round_id=? AND " + ("id=?" if plan_id else "rule='mes'")
    r = con.execute(q, (round_id, plan_id) if plan_id else (round_id,)).fetchone()
    return j(r["receipts"], {}).get(member_id, {}) if r else {}


# ----------------------------------------------------------------------------
# ranking (Schulze over members' real rankings) and ratification
# ----------------------------------------------------------------------------
def submit_ranking(con, round_id: str, member_id: str, ranking: list[str]) -> None:
    rnd = get_round(con, round_id)
    if rnd["status"] != "plans":
        raise PBError("ranking_closed")
    require_active(con, rnd["node_id"], member_id)
    valid = [p["id"] for p in plans_of(con, round_id)]
    if sorted(ranking) != sorted(valid):
        raise PBError("ranking_must_list_every_plan")
    with tx(con):
        con.execute("""INSERT INTO rankings (round_id,member_id,ranking,submitted_at) VALUES (?,?,?,?)
                       ON CONFLICT(round_id,member_id) DO UPDATE SET ranking=excluded.ranking, submitted_at=excluded.submitted_at""",
                    (round_id, member_id, json.dumps(ranking), now()))


def schulze(rankings: list[list[str]], candidates: list[str]) -> list[str]:
    n = len(candidates); ix = {c: i for i, c in enumerate(candidates)}
    d = [[0] * n for _ in range(n)]
    for r in rankings:
        pos = {c: k for k, c in enumerate(r)}
        for a in candidates:
            for b in candidates:
                if a != b and pos.get(a, n) < pos.get(b, n):
                    d[ix[a]][ix[b]] += 1
    p = [[d[i][k] if d[i][k] > d[k][i] else 0 for k in range(n)] for i in range(n)]
    for i in range(n):
        for jj in range(n):
            if i == jj: continue
            for k in range(n):
                if i != k and jj != k:
                    p[jj][k] = max(p[jj][k], min(p[jj][i], p[i][k]))
    wins = [sum(1 for k in range(n) if k != i and p[i][k] >= p[k][i]) for i in range(n)]
    return [c for _, c in sorted(zip(wins, candidates), key=lambda t: (-t[0], t[1]))]


def pick_winner(con, round_id: str) -> dict:
    """Schulze over real rankings; falls back to the MES plan when nobody ranked."""
    plans = plans_of(con, round_id)
    valid = [p for p in plans if p["valid"]]
    if not valid:
        raise PBError("no_valid_plan")
    rk = [j(r["ranking"], []) for r in rows(con.execute("SELECT ranking FROM rankings WHERE round_id=?", (round_id,)).fetchall())]
    if rk:
        order = schulze(rk, [p["id"] for p in valid])
        winner = next(p for p in valid if p["id"] == order[0])
    else:
        winner = next((p for p in valid if p["rule"] == "mes"), valid[0])
    with tx(con):
        con.execute("UPDATE rounds SET status='ratifying', winner_plan_id=? WHERE id=?", (winner["id"], round_id))
    return winner


def submit_ratification(con, round_id: str, member_id: str, plan_id: str, verdict: str) -> None:
    rnd = get_round(con, round_id)
    if rnd["status"] != "ratifying":
        raise PBError("ratification_closed")
    require_active(con, rnd["node_id"], member_id)
    if verdict not in ("approve", "reject"):
        raise PBError("bad_verdict")
    with tx(con):
        con.execute("""INSERT INTO ratifications (round_id,plan_id,member_id,verdict,submitted_at) VALUES (?,?,?,?,?)
                       ON CONFLICT(round_id,member_id) DO UPDATE SET plan_id=excluded.plan_id, verdict=excluded.verdict, submitted_at=excluded.submitted_at""",
                    (round_id, plan_id, member_id, verdict, now()))


def close_ratification(con, round_id: str, plan_id: str) -> dict:
    """60% of ELIGIBLE members must explicitly approve; abstention never counts as approval."""
    rnd = get_round(con, round_id)
    node = get_node(con, rnd["node_id"])
    eligible = len(active_members(con, rnd["node_id"]))
    votes = rows(con.execute("SELECT verdict FROM ratifications WHERE round_id=? AND plan_id=?", (round_id, plan_id)).fetchall())
    approvals = sum(1 for v in votes if v["verdict"] == "approve")
    rejections = len(votes) - approvals
    ok = eligible > 0 and approvals * 100 >= node["ratify_pct"] * eligible
    did = new_id("d_")
    with tx(con):
        con.execute("""INSERT INTO decisions (id,round_id,plan_id,verdict,rule,approvals,rejections,eligible,ballot_hash,decided_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (did, round_id, plan_id, "ratified" if ok else "failed", f"approval_{node['ratify_pct']}", approvals, rejections,
                     eligible, ballot_hash(con, round_id), now()))
        con.execute("UPDATE rounds SET status=? WHERE id=?", ("ratified" if ok else "failed", round_id))
        if ok:
            plan = row(con.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone())
            for pid, amt in j(plan["amounts"], {}).items():
                if amt <= 0: continue
                p = get_proposal(con, pid)
                con.execute("""INSERT INTO payouts (id,round_id,proposal_id,payee_member_id,amount_paise,kind,status,created_at)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (new_id("pay_"), round_id, pid, p["lead_id"], amt, "reimbursement", "queued", now()))
    return row(con.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone())


# ----------------------------------------------------------------------------
# payouts
# ----------------------------------------------------------------------------
def payout_queue(con, round_id: str | None = None) -> list[dict]:
    q = "SELECT p.*, pr.title, m.upi_id AS lead_upi, m.display AS lead_display FROM payouts p JOIN proposals pr ON pr.id=p.proposal_id LEFT JOIN members m ON m.id=p.payee_member_id WHERE p.status IN ('queued','sent')"
    args: tuple = ()
    if round_id:
        q += " AND p.round_id=?"; args = (round_id,)
    return rows(con.execute(q + " ORDER BY p.created_at", args).fetchall())


def upi_link(pa: str, amount_paise: int, note: str, pn: str = "") -> str:
    from urllib.parse import quote
    am = f"{amount_paise // 100}.{amount_paise % 100:02d}"
    return f"upi://pay?pa={quote(pa)}&pn={quote(pn or 'Public Bank')}&am={am}&cu=INR&tn={quote(note[:50])}"


def set_payee(con, payout_id: str, *, upi: str | None = None, kind: str | None = None) -> None:
    with tx(con):
        if upi: con.execute("UPDATE payouts SET payee_upi=? WHERE id=?", (upi.strip(), payout_id))
        if kind: con.execute("UPDATE payouts SET kind=? WHERE id=?", (kind, payout_id))


def mark_paid(con, payout_id: str, reference: str, note: str = "") -> None:
    with tx(con):
        con.execute("UPDATE payouts SET status='paid', reference=?, receipt_note=?, paid_at=? WHERE id=?",
                    (reference.strip()[:64], note.strip()[:200], now(), payout_id))
        rid = con.execute("SELECT round_id FROM payouts WHERE id=?", (payout_id,)).fetchone()[0]
        left = con.execute("SELECT COUNT(*) FROM payouts WHERE round_id=? AND status IN ('queued','sent')", (rid,)).fetchone()[0]
        if left == 0:
            con.execute("UPDATE rounds SET status='paid' WHERE id=?", (rid,))


# ----------------------------------------------------------------------------
# the five protocol objects
# ----------------------------------------------------------------------------
def export_node(con, node_id: str) -> dict[str, Any]:
    """Everything another node (or an auditor) may see. No member identities, no UPI ids."""
    node = get_node(con, node_id)
    members_n = len(active_members(con, node_id))
    out: dict[str, Any] = {
        "protocol": "public-bank/0.1",
        "node": {"id": node["id"], "name": node["name"], "kind": node["kind"], "parent": node["parent_id"],
                 "members": members_n, "ratification": {"rule": node["ratify_rule"], "threshold_pct": node["ratify_pct"]},
                 "allocation_rule": node["allocation_rule"], "levy_pct": node["levy_pct"]},
        "offers": [], "proposals": [], "decisions": [], "tallies": [], "contributions": [],
    }
    for o in rows(con.execute("SELECT * FROM offers WHERE node_id=? AND scope IN ('network','public') AND status IN ('offered','confirmed')", (node_id,)).fetchall()):
        out["offers"].append({"id": o["id"], "what": o["what"], "quantity": o["quantity"], "unit": o["unit"], "status": o["status"], "scope": o["scope"]})
    for r in rows(con.execute("SELECT * FROM rounds WHERE node_id=?", (node_id,)).fetchall()):
        for p in eligible_proposals(con, r["id"]):
            out["proposals"].append({"id": p["id"], "round": r["id"], "title": p["title"], "min": p["min_paise"], "ideal": p["ideal_paise"],
                                     "has_lead": bool(p["lead_id"]), "source": p["source"], "cosign_nodes": j(p["cosign_nodes"], [])})
        for d in rows(con.execute("SELECT * FROM decisions WHERE round_id=?", (r["id"],)).fetchall()):
            plan = row(con.execute("SELECT rule,amounts FROM plans WHERE id=?", (d["plan_id"],)).fetchone()) or {}
            out["decisions"].append({"id": d["id"], "round": r["id"], "verdict": d["verdict"], "rule": d["rule"],
                                     "plan_rule": plan.get("rule"), "amounts": j(plan.get("amounts"), {}), "decided_at": d["decided_at"]})
            out["tallies"].append({"decision": d["id"], "approvals": d["approvals"], "rejections": d["rejections"],
                                   "eligible": d["eligible"], "ballot_hash": d["ballot_hash"]})
    for c in rows(con.execute("SELECT * FROM contributions WHERE from_node_id=? OR to_node_id=?", (node_id, node_id)).fetchall()):
        out["contributions"].append({"id": c["id"], "from": c["from_node_id"], "to": c["to_node_id"], "round": c["round_id"], "amount": c["amount_paise"]})
    return out
