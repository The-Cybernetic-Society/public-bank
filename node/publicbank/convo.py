"""
Pure helpers shared by the Telegram bot and the WhatsApp adapter: money formatting, the ballot and rupee
parsers, the plan-rule names, user-facing error texts, and the proposal summary. Nothing here talks to a
transport or to the database beyond what it is handed.
"""
from __future__ import annotations

import re

from . import core, db

RULE_NAMES = {"mes": "Equal Shares", "nash": "Nash", "utilitarian": "Most points", "maximin": "Help the least served"}
ERRORS = {
    "proposals_closed": "Proposals are closed for this round.",
    "ballots_closed": "Ballots are closed for this round.",
    "ranking_closed": "Ranking is closed for this round.",
    "ratification_closed": "Approval is closed for this round.",
    "not_a_member": "The coordinator hasn't confirmed you yet.",
    "points_must_sum_100": "Points must add up to 100.",
    "unknown_proposal": "The list changed since you started. Start your ballot again.",
    "ranking_must_list_every_plan": "The plans changed since you started. Start your ranking again.",
}
STAGES = {"propose": ("proposals", "propose"), "ballot": ("ballots", "vote on"), "rank": ("plans", "rank"), "approve": ("ratifying", "approve")}


def rupees(p: int) -> str:
    return f"₹{p // 100:,}"


def err_text(e: core.PBError) -> str:
    return ERRORS.get(e.code, f"That didn't work ({e.code}{': ' + e.detail if e.detail else ''}).")


def parse_rupees(s: str) -> int | None:
    s = re.sub(r"[₹,\s]|rs\.?|inr", "", s.lower())
    return int(s) if s.isdigit() else None


def parse_points(text: str, ids: list[str]) -> dict[str, int] | str:
    """'40 30 30' (positional, optional trailing unspent) or '1:40 3:60 u:0'. Returns points or an error to show."""
    toks = text.replace(",", " ").split()
    pts: dict[str, int] = {}
    if any(":" in t or "=" in t for t in toks):
        for t in toks:
            k, _, v = t.replace("=", ":").partition(":")
            if not v.isdigit(): return f"Couldn't read '{t}'."
            if k.lower() in ("u", "unspent"): key = "_unspent"
            elif k.isdigit() and 1 <= int(k) <= len(ids): key = ids[int(k) - 1]
            else: return f"There's no item {k}."
            pts[key] = pts.get(key, 0) + int(v)
    else:
        if not all(t.isdigit() for t in toks) or len(toks) not in (len(ids), len(ids) + 1):
            return f"Send {len(ids)} whole numbers (or {len(ids) + 1} to leave some unspent)."
        pts = {k: int(v) for k, v in zip(ids + ["_unspent"], toks)}
    pts = {k: v for k, v in pts.items() if v}
    if sum(pts.values()) != 100:
        return f"Those add up to {sum(pts.values())}, not 100."
    return pts


def proposal_summary(d: dict) -> str:
    return (f"{d['title']}\n{d['text']}\n\nIdeal {rupees(d['ideal'] * 100)}, minimum {rupees(d['min'] * 100)}\n"
            + ("You lead it: you buy or organise it and are reimbursed against receipts." if d["lead"]
               else "No lead yet: it stays a draft, off the ballot, until someone agrees to lead it."))


def plan_lines(con, rnd: dict, plan: dict) -> list[str]:
    titles = {p["id"]: p["title"] for p in core.eligible_proposals(con, rnd["id"])}
    amts = db.j(plan["amounts"], {})
    return [f"  {titles.get(k, k)}: {rupees(v)}" for k, v in sorted(amts.items(), key=lambda kv: -kv[1]) if v > 0] or ["  (funds nothing)"]


def ballot_prompt(con, node: dict, rnd: dict, cur: dict) -> tuple[str, list[str], list[str]]:
    """The ballot question for a round: (text, proposal ids, titles). `cur` is the member's current points, if any."""
    props = core.eligible_proposals(con, rnd["id"])
    lines = [f"{node['name']} · budget {rupees(core.spendable_budget(con, rnd))}. Split 100 points across:"]
    for i, p in enumerate(props, 1):
        lines.append(f"{i}. {p['title']} (ideal {rupees(p['ideal_paise'])}, min {rupees(p['min_paise'])})" + (f" · now {cur[p['id']]}" if p["id"] in cur else ""))
    lines.append("\nReply with one number per item, in order, adding up to 100, e.g. " + " ".join(["0"] * len(props)).replace("0", "100", 1)
                 + "\nOr by item: 1:60 3:40. Add one more number (or u:N) to leave points unspent." + (f" Your current unspent: {cur['_unspent']}." if "_unspent" in cur else ""))
    return "\n".join(lines), [p["id"] for p in props], [p["title"] for p in props]


def receipt_line(con, rnd: dict, member_id: str) -> str | None:
    rec = core.receipt_for(con, rnd["id"], member_id)
    if not rec:
        return None
    tot = sum(rec.values()) or 1
    titles = {p["id"]: p["title"] for p in core.eligible_proposals(con, rnd["id"])}
    return ", ".join(f"{titles.get(k, k)} {v * 100 // tot}%" for k, v in sorted(rec.items(), key=lambda kv: -kv[1]))


def rounds_at(con, member_id: str, stage: str) -> list[tuple[dict, dict]]:
    """(node, round) for each node where the member is active and the current round is at `stage`."""
    out = []
    for x in db.rows(con.execute("SELECT node_id FROM memberships WHERE member_id=? AND status='active' ORDER BY node_id", (member_id,)).fetchall()):
        rnd = core.current_round(con, x["node_id"])
        if rnd and rnd["status"] == stage:
            out.append((core.get_node(con, x["node_id"]), rnd))
    return out
