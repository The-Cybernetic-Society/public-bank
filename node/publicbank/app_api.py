"""
The JSON API behind cyberneticsociety.com/app: an owner signs in with a code sent to the WhatsApp number their
business registered with, and from then on sees the Commons from the inside. Everything the public board hides
(which business backs what, who leads, your own ballot, your receipt) is here, behind the sign-in; nothing here
is readable without it. The static site keeps a bearer token (`auth.mint`) and sends it on every call.

  POST /auth/wa/start   {phone, cc}          → a code goes to that number on WhatsApp (if it is one of ours)
  POST /auth/wa/verify  {phone, cc, code}    → {token, member}
  GET  /me/home.json    [?node=]             → the whole picture for one business: the pool, the round, the fund,
                                               the proposals in the order that business should read them (with
                                               their place on the five trade-offs, their backers by name, their
                                               lead), the ballot, plans and receipt, the ledger, the businesses on
                                               the map, the twin's questions, the business's own scheme card
  POST /me/propose, /me/lead, /me/cosign, /me/ballot, /me/rank, /me/approve, /me/need, /me/values, /me/charter
  POST /me/profile      {display, business, place} → the person's name, the business's name and place (coordinator)
  POST /me/twin/answer  {key, answer}        → an answer to one of the twin's questions
  POST /me/twin/ask     {question, history}  → the twin's answer (the model when a key is set, else plain facts)

Every action ends in the same core call the web page and the bots make, and answers with the refreshed home, so
the page redraws from one source of truth. Only the origins in PB_APP_ORIGINS may call this from a browser.
"""
from __future__ import annotations

import json
import os
from datetime import date as _date, timedelta as _timedelta

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import auth, businesses, commonsmap, core, db, twin, whatsapp
from .convo import CHARTER_LINES, RULE_NAMES
from .twin import tags_for
from .web import ALIASES, HOME_POOL, LAUNCH, TIMELINE, _budget, _member, _resolve, app, con

ORIGINS = {o.strip().rstrip("/") for o in os.environ.get(
    "PB_APP_ORIGINS", "https://cyberneticsociety.com,https://www.cyberneticsociety.com,http://localhost:8031,http://127.0.0.1:8031").split(",") if o.strip()}
_PREFIXES = ("/auth/", "/me/", "/twin/")   # /twin/map.json and /twin/<id>/card.json feed the app's map of Goa
STAGE_WORDS = {"proposals": "Ideas on the table", "ballots": "Voting", "plans": "Ranking the plans", "ratifying": "Approving the plan",
               "ratified": "Approved", "failed": "Not approved", "paid": "Paid out", "closed": "Closed"}
NEXT_WORDS = {"proposals": "back or lead what your business would use, or add your own", "ballots": "split 100 points across the list",
              "plans": "rank the four plans", "ratifying": "approve or reject the winning plan", "ratified": "the treasurer pays the vendors",
              "paid": "every rupee is on the public record", "failed": "nothing was paid; the next round starts fresh", "closed": "wait for the next round"}


@app.middleware("http")
async def _app_cors(request: Request, call_next):
    """The static site is on another origin: let it in, and only it. Other paths are untouched."""
    path = request.url.path
    if not path.startswith(_PREFIXES):
        return await call_next(request)
    origin = (request.headers.get("origin") or "").rstrip("/")
    allowed = origin in ORIGINS
    if request.method == "OPTIONS":
        if not allowed:
            return Response("", status_code=403)
        return Response("", headers={"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                                     "Access-Control-Allow-Headers": "Authorization, Content-Type", "Access-Control-Max-Age": "86400", "Vary": "Origin"})
    try:
        resp = await call_next(request)
    except Exception:   # a page would get Starlette's 500; the site needs JSON it can read, with the CORS header
        import logging
        logging.getLogger("publicbank").exception("app api %s", path)
        resp = JSONResponse({"error": "server_error"}, status_code=500)
    if allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    if not path.startswith("/twin/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def _err(code: str, status: int = 400, detail: str = "") -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


async def _body(request: Request) -> dict:
    try:
        d = await request.json()
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _phone(d: dict) -> str:
    return core.normalize_phone(str(d.get("phone", "")), str(d.get("cc", "")))


def _masked(phone: str) -> str:
    return "•" * max(0, len(phone) - 4) + phone[-4:]


# ----------------------------------------------------------------------------
# sign in
# ----------------------------------------------------------------------------
@app.post("/auth/wa/start")
async def wa_start(request: Request):
    d = await _body(request)
    phone = _phone(d)
    if not phone:
        return _err("bad_phone")
    try:
        code = auth.start(con, phone)
    except core.PBError as e:
        if e.code == "unknown_phone":
            # the same answer as for a known number: nothing here says which numbers are ours
            return JSONResponse({"ok": True, "to": _masked(phone), "via": "none"})
        return _err(e.code, 429, e.detail)
    via = whatsapp.send_otp(phone, code)
    out = {"ok": True, "to": _masked(phone), "via": via or "none"}
    if whatsapp.NUMBER:
        out["wa_me"] = f"https://wa.me/{whatsapp.NUMBER}?text=LOGIN"
    return JSONResponse(out)


@app.post("/auth/wa/verify")
async def wa_verify(request: Request):
    d = await _body(request)
    phone = _phone(d)
    if not phone:
        return _err("bad_phone")
    try:
        m = auth.verify(con, phone, str(d.get("code", "")))
    except core.PBError as e:
        return _err(e.code, 401 if e.code in ("bad_code", "code_expired") else 429, e.detail)
    return JSONResponse({"token": auth.mint(m["id"]), "member": {"id": m["id"], "display": m.get("display")}})


# ----------------------------------------------------------------------------
# the home picture
# ----------------------------------------------------------------------------
def _require_json(request: Request) -> dict | None:
    return _member(request)


def _my_nodes(m: dict) -> list[dict]:
    """The businesses this member is active in, the ones they coordinate first, each with its pool."""
    q = """SELECT n.*, ms.role FROM nodes n JOIN memberships ms ON ms.node_id=n.id WHERE ms.member_id=? AND ms.status='active'
           ORDER BY ms.role='coordinator' DESC, n.parent_id IS NULL, n.id"""
    return db.rows(con.execute(q, (m["id"],)).fetchall())


def _pick_node(m: dict, want: str | None) -> dict | None:
    mine = _my_nodes(m)
    if want:
        for n in mine:
            if n["id"] == want:
                return n
    return mine[0] if mine else None


def _timeline(today: _date | None = None) -> list[dict]:
    today = today or _date.today()
    marks = [(LAUNCH + _timedelta(days=d), label, note) for d, label, note in TIMELINE]
    now = next((i for i, (d, _, _) in enumerate(marks) if d >= today), len(marks))
    return [{"date": d.isoformat(), "label": label, "note": note, "state": "done" if i < now else "next" if i == now else "later"}
            for i, (d, label, note) in enumerate(marks)]


def _proposals(node: dict, pool: dict, rnd: dict, m: dict) -> list[dict]:
    """Every proposal in the round, in the order this business should read them (`twin.rank_for`), with the five
    trade-off tags, backers and lead by business name, and what this member has done about each."""
    props = db.rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND status IN ('draft','eligible') ORDER BY created_at", (rnd["id"],)).fetchall())
    kids = core.children(con, pool["id"])
    names = {k["id"]: k["name"] for k in kids}
    member_node: dict[str, str] = {}
    for k in kids:
        for mm in core.active_members(con, k["id"]):
            member_node.setdefault(mm["id"], k["id"])
    ranked = twin.rank_for(con, node["id"], props, limit=len(props) or 1)
    out = []
    for p in ranked:
        signs = db.j(p["cosign_nodes"], [])
        t = tags_for(p["title"] + " " + (p.get("text") or ""))
        lead_node = member_node.get(p.get("lead_id") or "")
        out.append({"id": p["id"], "title": p["title"], "text": (p.get("text") or "")[:1200], "ideal": p["ideal_paise"] // 100, "min": p["min_paise"] // 100,
                    "status": p["status"], "source": p["source"], "created_at": p["created_at"],
                    "backers": [names.get(x, x) for x in signs], "backer_ids": signs,
                    "lead": names.get(lead_node, lead_node) if lead_node else None, "lead_is_me": p.get("lead_id") == m["id"],
                    "backed_by_me": node["id"] in signs, "suggested": bool(p.get("suggested")), "score": p.get("score", 0),
                    "tags": {k: t.get(k, 0.0) for k, *_ in core.VALUE_DIMENSIONS}, "spoke": [k for k, c in t["_conf"].items() if c],
                    "scheme": db.j(p.get("scheme_json"), None) if p.get("scheme_json") else None,
                    "built_from": [names[nid] for nid, nm in names.items() if f"- {nm}:" in (p.get("text") or "")]})
    return out


def _plans(rnd: dict, m: dict, titles: dict) -> dict:
    plans = core.plans_of(con, rnd["id"])
    rec = core.receipt_for(con, rnd["id"], m["id"]) if plans else {}
    tot = sum(rec.values()) or 1
    mine = db.row(con.execute("SELECT ranking FROM rankings WHERE round_id=? AND member_id=?", (rnd["id"], m["id"])).fetchone())
    return {"plans": [{"id": pl["id"], "rule": pl["rule"], "name": RULE_NAMES.get(pl["rule"], pl["rule"]), "valid": bool(pl["valid"]),
                       "amounts": {k: v // 100 for k, v in db.j(pl["amounts"], {}).items()}, "winner": pl["id"] == rnd.get("winner_plan_id")} for pl in plans],
            "receipt": [{"id": k, "title": titles.get(k, k), "pct": v * 100 // tot} for k, v in sorted(rec.items(), key=lambda kv: -kv[1])],
            "my_ranking": db.j(mine["ranking"], []) if mine else []}


def _home(m: dict, want: str | None = None) -> dict:
    node = _pick_node(m, want)
    if not node:
        return {"member": {"id": m["id"], "display": m.get("display")}, "business": None, "businesses": [],
                "note": "This number is signed in but is not in any business yet. Register at the Commons first."}
    pool = core.get_node(con, node["parent_id"]) if node.get("parent_id") else (node if node.get("kind") == "pool" else None)
    rnd = core.current_round(con, pool["id"]) if pool else core.current_round(con, node["id"])
    app_ = core.application_of_node(con, node["id"])
    b = _budget(pool or node, rnd)
    biz = commonsmap.businesses(con, pool["id"]) if pool else []
    me_biz = next((x for x in biz if x["node_id"] == node["id"]), None)
    needs = core.needs_of(con, node["id"], include_children=False, status=None)
    vv = core.values_of(con, node["id"], include_children=False)
    out = {
        "member": {"id": m["id"], "display": m.get("display"), "role": node.get("role")},
        "business": {"id": node["id"], "name": node["name"], "place": (me_biz or {}).get("place"), "place_id": (me_biz or {}).get("place_id"),
                     "verified": bool(app_ and app_["status"] == "verified"), "charter": bool(node.get("charter_at")),
                     "coordinator": node.get("role") == "coordinator", "team": len(core.active_members(con, node["id"])),
                     "needs": [{"id": n["id"], "text": n["text"], "status": n["status"]} for n in needs],
                     "values": vv[-1]["vector"] if vv else None},
        "businesses": [{"id": n["id"], "name": n["name"], "role": n["role"]} for n in _my_nodes(m)],
        "pool": {"id": pool["id"], "name": pool["name"], "cosign_min": pool.get("cosign_min") or 0, "pool_vote": pool.get("pool_vote"),
                 "charter": CHARTER_LINES, "shops": len(biz), "verified": sum(1 for x in biz if x["verified"])} if pool else None,
        "round": None, "fund": {k: (v // 100 if isinstance(v, int) and k != "proposals" else v) for k, v in b.items()},
        "timeline": _timeline(), "axes": [{"key": k, "left": l, "right": r, "hint": h} for k, l, r, h in core.VALUE_DIMENSIONS],
        "map": {"bbox": None, "businesses": []}, "proposals": [], "ballot": None, "plans": None, "decision": None, "ledger": [],
        "questions": [], "schemes": None, "as_of": core._ist(core.now()),
    }
    if biz:
        d = commonsmap.data(con, pool["id"])
        out["map"] = {"bbox": d["bbox"], "context": d.get("context", []),
                      "businesses": [{"id": x["node_id"], "name": x["name"], "place": x["place"], "verified": x["verified"], "x": x["x"], "y": x["y"],
                                      "me": x["node_id"] == node["id"], "need": x["need"]} for x in biz]}
    if rnd:
        scope = pool or node
        out["round"] = {"id": rnd["id"], "stage": rnd["status"], "stage_word": STAGE_WORDS.get(rnd["status"], rnd["status"]),
                        "next": NEXT_WORDS.get(rnd["status"], ""), "pot": core.spendable_budget(con, rnd) // 100,
                        "can_ballot": core.can_ballot(con, rnd, m["id"]) if rnd["status"] == "ballots" else False,
                        "ballots_in": len(core.ballots_of(con, rnd["id"])), "voters": len(core.voters(con, rnd)),
                        "eligible_for_vote": bool(app_ is None or app_["status"] == "verified")}
        out["proposals"] = _proposals(node, scope, rnd, m)
        titles = {p["id"]: p["title"] for p in out["proposals"]}
        mine = db.row(con.execute("SELECT points, revision FROM ballots WHERE round_id=? AND member_id=?", (rnd["id"], m["id"])).fetchone())
        out["ballot"] = {"points": db.j(mine["points"], {}), "revision": mine["revision"]} if mine else None
        if rnd["status"] in ("plans", "ratifying", "ratified", "failed", "paid", "closed"):
            out["plans"] = _plans(rnd, m, titles)
            rt = db.row(con.execute("SELECT verdict FROM ratifications WHERE round_id=? AND member_id=?", (rnd["id"], m["id"])).fetchone())
            out["my_verdict"] = rt["verdict"] if rt else None
            dec = db.row(con.execute("SELECT * FROM decisions WHERE round_id=? ORDER BY decided_at DESC LIMIT 1", (rnd["id"],)).fetchone())
            if dec:
                out["decision"] = {"verdict": dec["verdict"], "approvals": dec["approvals"], "rejections": dec["rejections"], "eligible": dec["eligible"]}
            out["ledger"] = [{"title": x["title"], "amount": x["amount_paise"] // 100, "status": x["status"], "reference": x["reference"]} for x in db.rows(con.execute(
                "SELECT p.status, p.amount_paise, p.reference, pr.title FROM payouts p JOIN proposals pr ON pr.id=p.proposal_id WHERE p.round_id=? ORDER BY p.created_at", (rnd["id"],)).fetchall())]
    out["choices"] = _choices(m, node, out)
    out["business"]["values_after"] = (core.values_of(con, node["id"], phase="after", include_children=False) or [{"vector": None}])[-1]["vector"]
    out["questions"] = twin.questions_for(con, m, node, pool, rnd)
    try:
        card = businesses.card(con, node["id"])
        out["schemes"] = {"summary": card["summary"], "slots": card["slots"], "unlocks": card["unlocks"]}
    except Exception:   # no catalogue on this install: the page simply shows no scheme card
        out["schemes"] = None
    return out


def _choices(m: dict, node: dict, out: dict) -> dict:
    """Everything this person has chosen, listed: what they backed, lead, put forward, their ballot, ranking and verdict,
    and the answers they gave the twin. Titles come from the current round; earlier rounds show by id."""
    props = {p["id"]: p for p in out.get("proposals", [])}
    mine = db.rows(con.execute("SELECT id, title, status, ideal_paise, created_at FROM proposals WHERE created_by=? ORDER BY created_at DESC", (m["id"],)).fetchall())
    answers = []
    for r in db.rows(con.execute("SELECT kind, key, answer, created_at FROM twin_answers WHERE member_id=? ORDER BY created_at DESC", (m["id"],)).fetchall()):
        title = props[r["key"]]["title"] if r["kind"] == "use" and r["key"] in props else None
        if r["kind"] == "use" and not title:
            t = db.row(con.execute("SELECT title FROM proposals WHERE id=?", (r["key"],)).fetchone())
            title = t["title"] if t else None
        answers.append({"kind": r["kind"], "key": r["key"], "answer": r["answer"], "title": title, "at": core._ist(r["created_at"])})
    ballot = out.get("ballot")
    return {"backed": [{"id": p["id"], "title": p["title"], "ideal": p["ideal"]} for p in props.values() if p["backed_by_me"]],
            "led": [{"id": p["id"], "title": p["title"], "ideal": p["ideal"]} for p in props.values() if p["lead_is_me"]],
            "proposed": [{"id": p["id"], "title": p["title"], "ideal": p["ideal_paise"] // 100, "status": p["status"]} for p in mine],
            "ballot": [{"id": k, "title": props[k]["title"] if k in props else ("left unspent" if k == "_unspent" else k), "points": v} for k, v in sorted(ballot["points"].items(), key=lambda kv: -kv[1])] if ballot else [],
            "ranking": [next((pl["name"] for pl in (out.get("plans") or {}).get("plans", []) if pl["id"] == pid), pid) for pid in ((out.get("plans") or {}).get("my_ranking") or [])],
            "verdict": out.get("my_verdict"), "answers": answers}


@app.post("/me/profile")
async def me_profile(request: Request):
    """The person's name, and, for a coordinator, the business's name and where it is (a place name, as on the form)."""
    def go(m, d, node_id):
        from . import places
        node = _pick_node(m, node_id)
        if not node:
            raise core.PBError("not_a_member")
        display = " ".join(str(d.get("display", "")).split())[:80]
        with db.tx(con):
            if display:
                con.execute("UPDATE members SET display=? WHERE id=?", (display, m["id"]))
        changed = {"display": bool(display)}
        if "business" in d or "place" in d:
            if not core.is_role(con, node["id"], m["id"], ("coordinator",)):
                raise core.PBError("not_coordinator")
            name = " ".join(str(d.get("business", "")).split())[:80]
            place_text = " ".join(str(d.get("place", "")).split())[:80]
            place = places.resolve(place_text) if place_text else None
            if place_text and not place:
                raise core.PBError("unknown_place", place_text)
            with db.tx(con):
                if name:
                    con.execute("UPDATE nodes SET name=? WHERE id=?", (name, node["id"]))
                    con.execute("UPDATE applications SET business=?, updated_at=? WHERE node_id=?", (name, db.now(), node["id"]))
                if place:
                    con.execute("UPDATE nodes SET place_id=? WHERE id=?", (place["id"], node["id"]))
                    con.execute("UPDATE applications SET place_id=?, updated_at=? WHERE node_id=?", (place["id"], db.now(), node["id"]))
            changed.update({"business": bool(name), "place": place["name"] if place else None})
        return changed
    return await _run(request, go)


@app.get("/me/home.json")
def me_home(request: Request, node: str = ""):
    m = _member(request)
    if not m:
        return _err("sign_in", 401)
    return JSONResponse(_home(m, node or None))


# ----------------------------------------------------------------------------
# actions: each one core call, then the refreshed home
# ----------------------------------------------------------------------------
def _act(request: Request):
    m = _member(request)
    if not m:
        raise core.PBError("sign_in")
    return m


def _round_for(m: dict, node_id: str | None) -> tuple[dict, dict, dict]:
    node = _pick_node(m, node_id)
    if not node:
        raise core.PBError("not_a_member")
    pool = core.get_node(con, node["parent_id"]) if node.get("parent_id") else node
    rnd = core.current_round(con, pool["id"])
    if not rnd:
        raise core.PBError("no_open_round")
    return node, pool, rnd


def _done(m: dict, node_id: str | None, extra: dict | None = None) -> JSONResponse:
    m = db.row(con.execute("SELECT * FROM members WHERE id=?", (m["id"],)).fetchone()) or m   # the action may have changed the member
    h = _home(m, node_id)
    if extra:
        h["result"] = extra
    return JSONResponse(h)


async def _run(request: Request, fn):
    """Sign-in, body, the action, the refreshed home; a PBError becomes {error} with the right status."""
    try:
        m = _act(request)
        d = await _body(request)
        node_id = d.get("node") or None
        extra = fn(m, d, node_id)
        return _done(m, node_id, extra)
    except core.PBError as e:
        status = 401 if e.code == "sign_in" else 403 if e.code in ("not_a_member", "not_coordinator", "not_yours") else 404 if e.code.endswith("not_found") else 400
        return _err(e.code, status, e.detail)


@app.post("/me/propose")
async def me_propose(request: Request):
    def go(m, d, node_id):
        node, pool, rnd = _round_for(m, node_id)
        title = " ".join(str(d.get("title", "")).split())[:120]
        text = " ".join(str(d.get("text", "")).split())[:2000] or title
        try:
            ideal = int(d.get("ideal") or 0); mn = int(d.get("min") or 0)
        except (TypeError, ValueError):
            raise core.PBError("bad_amounts")
        if len(title) < 3 or ideal <= 0:
            raise core.PBError("bad_text")
        p = core.add_proposal(con, rnd["id"], m["id"], title=title, text=text, ideal_paise=ideal * 100, min_paise=max(0, mn) * 100,
                              lead_id=m["id"] if d.get("lead", True) else None, payee_upi=str(d.get("upi") or "").strip())
        return {"proposal": p["id"], "status": p["status"]}
    return await _run(request, go)


@app.post("/me/lead")
async def me_lead(request: Request):
    def go(m, d, node_id):
        p = core.lead_proposal(con, str(d.get("pid", "")), m["id"])
        return {"proposal": p["id"], "status": p["status"]}
    return await _run(request, go)


@app.post("/me/cosign")
async def me_cosign(request: Request):
    def go(m, d, node_id):
        node, pool, rnd = _round_for(m, node_id)
        p = core.cosign_proposal(con, str(d.get("pid", "")), node["id"], m["id"])
        return {"proposal": p["id"], "status": p["status"], "backers": len(db.j(p["cosign_nodes"], []))}
    return await _run(request, go)


@app.post("/me/ballot")
async def me_ballot(request: Request):
    def go(m, d, node_id):
        node, pool, rnd = _round_for(m, node_id)
        pts = d.get("points") or {}
        if not isinstance(pts, dict):
            raise core.PBError("points_must_sum_100")
        try:
            clean = {str(k): int(v) for k, v in pts.items() if int(v) > 0}
        except (TypeError, ValueError):
            raise core.PBError("points_must_sum_100")
        b = core.submit_ballot(con, rnd["id"], m["id"], clean)
        return {"revision": b["revision"]}
    return await _run(request, go)


@app.post("/me/rank")
async def me_rank(request: Request):
    def go(m, d, node_id):
        node, pool, rnd = _round_for(m, node_id)
        order = [str(x) for x in (d.get("order") or []) if isinstance(x, str)]
        seen: list[str] = []
        [seen.append(x) for x in order if x not in seen]
        rest = [p["id"] for p in core.plans_of(con, rnd["id"]) if p["id"] not in seen]
        core.submit_ranking(con, rnd["id"], m["id"], seen + rest)
        return {"ranked": len(seen + rest)}
    return await _run(request, go)


@app.post("/me/approve")
async def me_approve(request: Request):
    def go(m, d, node_id):
        node, pool, rnd = _round_for(m, node_id)
        if not rnd.get("winner_plan_id"):
            raise core.PBError("wrong_stage")
        verdict = "approve" if d.get("verdict") == "approve" else "reject"
        core.submit_ratification(con, rnd["id"], m["id"], rnd["winner_plan_id"], verdict)
        return {"verdict": verdict}
    return await _run(request, go)


@app.post("/me/need")
async def me_need(request: Request):
    def go(m, d, node_id):
        node = _pick_node(m, node_id)
        if not node:
            raise core.PBError("not_a_member")
        n = core.add_need(con, node["id"], m["id"], str(d.get("text", "")))
        if node.get("parent_id"):
            twin.schedule(node["parent_id"])
        return {"need": n["id"]}
    return await _run(request, go)


@app.post("/me/values")
async def me_values(request: Request):
    def go(m, d, node_id):
        node = _pick_node(m, node_id)
        if not node:
            raise core.PBError("not_a_member")
        vec = d.get("vector") or {}
        if not isinstance(vec, dict):
            raise core.PBError("bad_text")
        phase = "after" if d.get("phase") == "after" else "before"
        v = core.set_values(con, node["id"], m["id"], vec, phase=phase)
        return {"vector": v, "phase": phase}
    return await _run(request, go)


@app.post("/me/charter")
async def me_charter(request: Request):
    def go(m, d, node_id):
        node = _pick_node(m, node_id)
        if not node or not node.get("parent_id"):
            raise core.PBError("no_parent")
        if not core.is_role(con, node["id"], m["id"], ("coordinator",)):
            raise core.PBError("not_coordinator")
        with db.tx(con):
            con.execute("UPDATE nodes SET charter_at=COALESCE(charter_at,?) WHERE id=?", (db.now(), node["id"]))
        twin.schedule(node["parent_id"])
        return {"charter": True}
    return await _run(request, go)


@app.post("/me/twin/answer")
async def me_twin_answer(request: Request):
    """One answer to a question the twin asked. `use:<pid>` with yes backs the proposal; everything else is kept
    as an answer (and `need`/`values` go through their own endpoints from the page)."""
    def go(m, d, node_id):
        node = _pick_node(m, node_id)
        if not node:
            raise core.PBError("not_a_member")
        key = str(d.get("key", ""))[:80]
        answer = str(d.get("answer", ""))[:500]
        kind, _, rest = key.partition(":")
        if kind == "use" and rest:
            core.record_twin_answer(con, m["id"], node["id"], "use", rest, "yes" if answer.lower().startswith("y") else "no")
            if answer.lower().startswith("y"):
                p = core.cosign_proposal(con, rest, node["id"], m["id"])
                return {"backed": p["id"], "status": p["status"]}
            return {"noted": key}
        if kind in ("leftover",) and rest:
            core.record_twin_answer(con, m["id"], node["id"], kind, rest, answer)
            return {"noted": key}
        core.record_twin_answer(con, m["id"], node["id"], kind or "other", rest or key, answer)
        return {"noted": key}
    return await _run(request, go)


@app.post("/me/twin/ask")
async def me_twin_ask(request: Request):
    m = _member(request)
    if not m:
        return _err("sign_in", 401)
    d = await _body(request)
    q = " ".join(str(d.get("question", "")).split())
    if len(q) < 2:
        return _err("bad_text")
    h = _home(m, d.get("node") or None)
    ctx = {"pool": h.get("pool") and {"name": h["pool"]["name"], "shops": h["pool"]["shops"], "verified": h["pool"]["verified"], "rules": h["pool"]["charter"]},
           "round": h.get("round") and {"stage": h["round"]["stage"], "pot": h["round"]["pot"], "what_next": h["round"]["next"], "fund": h["fund"]},
           "business": h.get("business") and {"name": h["business"]["name"], "place": h["business"]["place"], "verified": h["business"]["verified"],
                                              "needs": [n["text"] for n in h["business"]["needs"]], "leans": h["business"]["values"]},
           "proposals": [{"title": p["title"], "ideal": p["ideal"], "backers": len(p["backers"]), "lead": bool(p["lead"]), "suggested": p["suggested"],
                          "ai_draft": p["source"] == "twin", "backed_by_me": p["backed_by_me"], "scheme": (p["scheme"] or {}).get("name") if p.get("scheme") else None}
                         for p in h.get("proposals", [])[:16]],
           "schemes": [{"name": s["name"], "status": s["status"], "department": s.get("department"), "max_rupees": s.get("max_rupees")} for s in ((h.get("schemes") or {}).get("slots") or [])],
           "timeline": h.get("timeline")}
    hist = d.get("history") if isinstance(d.get("history"), list) else []
    return JSONResponse(twin.ask(con, q, ctx, hist))
