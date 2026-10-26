"""
MCP server for the Public Bank mini-app: what a resident's agent calls.

Contract (agentvillage-app src/lib/server/mini-apps/dispatch/*):
  - Streamable HTTP MCP: initialize → notifications/initialized → tools/call, JSON or SSE answers.
  - Resident identity, one of:
      * `X-Village-Subject`: a per-app pseudonymous resident reference the dispatcher sends under a shared
        app token (`service.auth.mode: "app-token"` + a `subject` separation declaration), or
      * an OAuth bearer per resident (`service.auth.mode: "oauth"`), later.
    The app token itself is checked as `Authorization: Bearer <PB_APP_TOKEN>`.
  - Writes carry `Idempotency-Key`; we store and replay the first response for a repeated key.
  - Every tool answers a closed JSON object; failures are short codes, never upstream text.

Nothing here reads the resident's agent, memory, or person model. The agent brings what it wants to
propose; the resident approves the call in their own approval gate.
"""
from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from . import core, db

APP_TOKEN = os.environ.get("PB_APP_TOKEN", "")
NODE_DEFAULT = os.environ.get("PB_DEFAULT_NODE", "goa-2026")

mcp = MCPServer("publicbank")
_con = db.connect()


def _h(ctx: Context) -> dict[str, str]:
    return {k.lower(): v for k, v in (ctx.headers or {}).items()}


def _member(ctx: Context) -> dict:
    """Resolve the calling resident from the dispatcher's headers. Fails closed."""
    req_h = _h(ctx)
    auth = req_h.get("authorization", "")
    if APP_TOKEN and auth != f"Bearer {APP_TOKEN}":
        raise core.PBError("unauthorized")
    subject = req_h.get("x-village-subject", "").strip().lower()
    if not (len(subject) == 32 and all(c in "0123456789abcdef" for c in subject)):
        raise core.PBError("no_subject")
    return core.upsert_member(_con, verified_by="miniapp", village_subject=subject)


def _idem(key: str | None, make: Any) -> dict:
    if not key:
        return make()
    r = _con.execute("SELECT response FROM idempotency WHERE key=?", (key,)).fetchone()
    if r:
        return json.loads(r["response"])
    out = make()
    with db.tx(_con):
        _con.execute("INSERT OR IGNORE INTO idempotency (key,response,created_at) VALUES (?,?,?)", (key, json.dumps(out), db.now()))
    return out


def _ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def _err(e: core.PBError) -> dict:
    return {"ok": False, "code": e.code}


def _node_for(m: dict, node_id: str | None) -> str:
    """The node the resident is acting in: an explicit id they are an active member of, else their first."""
    if node_id:
        core.require_active(_con, node_id, m["id"])
        return node_id
    r = _con.execute("SELECT node_id FROM memberships WHERE member_id=? AND status='active' ORDER BY joined_at LIMIT 1", (m["id"],)).fetchone()
    if not r:
        raise core.PBError("not_a_member")
    return r["node_id"]


@mcp.tool(name="publicbank_status")
def status(ctx: Context, node: str | None = None) -> dict:
    """What the resident's node is doing now: the round's stage, the proposal list, the plans (once computed),
    the resident's own Equal Shares receipt, and whether their ballot and approval are in."""
    try:
        m = _member(ctx); nid = _node_for(m, node)
        rnd = core.current_round(_con, nid)
        if not rnd:
            return _ok(node=nid, round=None)
        props = [{"id": p["id"], "title": p["title"], "min": p["min_paise"], "ideal": p["ideal_paise"], "source": p["source"]}
                 for p in core.eligible_proposals(_con, rnd["id"])]
        plans = [{"id": p["id"], "rule": p["rule"], "amounts": json.loads(p["amounts"]), "valid": bool(p["valid"])} for p in core.plans_of(_con, rnd["id"])]
        mine = {
            "ballot_in": bool(_con.execute("SELECT 1 FROM ballots WHERE round_id=? AND member_id=?", (rnd["id"], m["id"])).fetchone()),
            "ranking_in": bool(_con.execute("SELECT 1 FROM rankings WHERE round_id=? AND member_id=?", (rnd["id"], m["id"])).fetchone()),
            "ratified": (r := _con.execute("SELECT verdict FROM ratifications WHERE round_id=? AND member_id=?", (rnd["id"], m["id"])).fetchone()) and r["verdict"],
            "receipt": core.receipt_for(_con, rnd["id"], m["id"]),
        }
        return _ok(node=nid, round={"id": rnd["id"], "stage": rnd["status"], "budget": core.spendable_budget(_con, rnd),
                                    "proposals": props, "plans": plans}, mine=mine)
    except core.PBError as e:
        return _err(e)


@mcp.tool(name="publicbank_propose")
def propose(ctx: Context, title: str, text: str, ideal_rupees: int, min_rupees: int = 0, lead: bool = True, node: str | None = None) -> dict:
    """Submit a budget proposal to the resident's node for the current round. `lead=true` means the resident
    agrees to lead it (required before it can be funded). Amounts in rupees."""
    def make():
        try:
            m = _member(ctx); nid = _node_for(m, node)
            rnd = core.current_round(_con, nid)
            if not rnd: return _err(core.PBError("no_open_round"))
            p = core.add_proposal(_con, rnd["id"], m["id"], title=title, text=text, ideal_paise=int(ideal_rupees) * 100,
                                  min_paise=int(min_rupees) * 100, lead_id=m["id"] if lead else None, source="agent")
            return _ok(proposal={"id": p["id"], "status": p["status"]})
        except core.PBError as e:
            return _err(e)
    return _idem(_h(ctx).get("idempotency-key"), make)


@mcp.tool(name="publicbank_lead")
def lead(ctx: Context, proposal_id: str) -> dict:
    """The resident agrees to lead an existing proposal (for example one suggested by the twin or another agent),
    which makes it eligible for funding."""
    def make():
        try:
            m = _member(ctx); p = core.lead_proposal(_con, proposal_id, m["id"])
            return _ok(proposal={"id": p["id"], "status": p["status"]})
        except core.PBError as e:
            return _err(e)
    return _idem(_h(ctx).get("idempotency-key"), make)


@mcp.tool(name="publicbank_offer")
def offer(ctx: Context, what: str, quantity: int = 1, unit: str = "items", scope: str = "node", node: str | None = None) -> dict:
    """Record something the resident can contribute to the node (time, items, a skill), confirmed for this pot.
    `scope` is who may see it: node, network, or public."""
    def make():
        try:
            m = _member(ctx); nid = _node_for(m, node)
            if scope not in ("node", "network", "public"): return _err(core.PBError("bad_scope"))
            o = core.add_offer(_con, nid, m["id"], what=what, quantity=quantity, unit=unit, scope=scope)
            core.confirm_offer(_con, o["id"], m["id"])
            return _ok(offer={"id": o["id"], "what": o["what"], "quantity": o["quantity"], "unit": o["unit"]})
        except core.PBError as e:
            return _err(e)
    return _idem(_h(ctx).get("idempotency-key"), make)


@mcp.tool(name="publicbank_ballot")
def ballot(ctx: Context, points: dict[str, int], node: str | None = None) -> dict:
    """Submit the resident's 100-point ballot: {proposal_id: points}. Points must sum to 100; "_unspent" is allowed.
    The agent may draft this from what it knows of the resident; the resident approves before it is sent."""
    def make():
        try:
            m = _member(ctx); nid = _node_for(m, node)
            rnd = core.current_round(_con, nid)
            if not rnd: return _err(core.PBError("no_open_round"))
            b = core.submit_ballot(_con, rnd["id"], m["id"], {k: int(v) for k, v in points.items()})
            return _ok(ballot={"revision": b["revision"]})
        except (core.PBError, ValueError) as e:
            return _err(e if isinstance(e, core.PBError) else core.PBError("bad_points"))
    return _idem(_h(ctx).get("idempotency-key"), make)


@mcp.tool(name="publicbank_approve")
def approve(ctx: Context, verdict: str, node: str | None = None) -> dict:
    """Approve or reject the winning plan in the ratification stage. verdict: "approve" | "reject"."""
    def make():
        try:
            m = _member(ctx); nid = _node_for(m, node)
            rnd = core.current_round(_con, nid)
            if not rnd or rnd["status"] != "ratifying": return _err(core.PBError("ratification_closed"))
            core.submit_ratification(_con, rnd["id"], m["id"], rnd["winner_plan_id"], verdict)
            return _ok(verdict=verdict)
        except core.PBError as e:
            return _err(e)
    return _idem(_h(ctx).get("idempotency-key"), make)


app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
