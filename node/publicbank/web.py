"""
Web layer: the three pages a resident touches (propose, ballot, approve), the coordinator's member list,
the treasurer's payout queue, sign-in with Edge City (third-party auth), and the public export.

Identity on the web is a signed session cookie holding our internal member id. A member gets one by:
  * tapping a join link (`/j/<token>`), which also links their Telegram id when it came from the bot;
  * signing in with Edge City (`/signin`), which links their EdgeOS human id.
Both land on the same member row (core.upsert_member links identities when they meet).

EdgeOS third-party auth (as the Agent Village app does it, src/lib/server/edgeos.ts):
  POST {EDGEOS_API_BASE}/auth/human/third-party/login   {email}            (app key as Bearer)
  POST {EDGEOS_API_BASE}/auth/human/third-party/verify  {email, code}      → token pair
  GET  {EDGEOS_API_BASE}/humans/me                        (resident token)   → id, names, telegram
The exact verify path and response shape are confirmed against the app's edgeos.ts once the key arrives;
until EDGEOS_APP_KEY is set, /signin is a 404 and the join-link path is the only door.

HARD RULE: the resident's EdgeOS token is used for `/humans/me` and nothing else. No directory reads.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from . import core, db

SECRET = os.environ.get("PB_SECRET", "dev-secret-change-me")
EDGEOS_API_BASE = os.environ.get("EDGEOS_API_BASE", "https://api.edgeos.world/api/v1").rstrip("/")
EDGEOS_APP_KEY = os.environ.get("EDGEOS_APP_KEY", "")
EDGEOS_POPUP_ID = os.environ.get("EDGEOS_POPUP_ID", "")
EDGEOS_ALLOWED_PATHS = ("/humans/me",)   # the only thing a resident token is ever used for
BOT_USERNAME = os.environ.get("PB_BOT_USERNAME", "")
BASE_URL = os.environ.get("PB_BASE_URL", "http://localhost:8000").rstrip("/")

signer = URLSafeSerializer(SECRET, salt="pb-session")
app = FastAPI(title="Public Bank node")
con = db.connect()


@app.exception_handler(core.PBError)
async def _pb_error(_request: Request, e: core.PBError):
    status = 403 if e.code in ("not_a_member", "not_coordinator", "not_yours", "unauthorized") else 404 if e.code.endswith("not_found") else 400
    return JSONResponse({"error": e.code, "detail": e.detail}, status_code=status)


# ----------------------------------------------------------------------------
# session
# ----------------------------------------------------------------------------
def _set_session(resp, member_id: str):
    resp.set_cookie("pb", signer.dumps({"m": member_id}), httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return resp


def _member(request: Request) -> dict | None:
    c = request.cookies.get("pb")
    if not c:
        return None
    try:
        mid = signer.loads(c)["m"]
    except (BadSignature, KeyError):
        return None
    return db.row(con.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone())


def _require(request: Request) -> dict:
    m = _member(request)
    if not m:
        raise HTTPException(401, "Sign in first.")
    return m


def _rupees(p: int) -> str:
    return f"₹{p // 100:,}"


# ----------------------------------------------------------------------------
# minimal HTML
# ----------------------------------------------------------------------------
CSS = """<style>:root{color-scheme:light dark}body{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:0 auto;padding:16px}
h1{font-size:22px}h2{font-size:17px;margin-top:28px}.card{border:1px solid #8884;border-radius:8px;padding:12px;margin:10px 0}
input,textarea,select,button{font:inherit;padding:8px;border:1px solid #8886;border-radius:6px;width:100%;box-sizing:border-box;margin:4px 0}
button{background:#0b6e4f;color:#fff;border:0;cursor:pointer}.muted{color:#777;font-size:14px}.row{display:flex;gap:8px;align-items:center}
.row input{width:90px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:6px;border-top:1px solid #8884;text-align:left}
.bad{color:#b00}.ok{color:#0b6e4f}</style>"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{title}</title>{CSS}<h1>{title}</h1>{body}")


def esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ----------------------------------------------------------------------------
# join and sign-in
# ----------------------------------------------------------------------------
@app.get("/j/{token}/poster.png")
def poster(token: str, via: str = ""):
    """A printable A5-ish poster for a join link: QR + node name. Public, but the token is the secret."""
    t = None
    for kind in ("join", "join_coord"):
        try:
            t = core.read_token(con, token, kind); break
        except core.PBError:
            pass
    if not t:
        raise HTTPException(404)
    node = core.get_node(con, t["node_id"])
    parent = core.get_node(con, node["parent_id"]) if node.get("parent_id") else None
    import io
    import qrcode
    from PIL import Image, ImageDraw, ImageFont
    from fastapi.responses import Response
    url = f"{BASE_URL}/j/{token}"
    if via == "wa":   # a WhatsApp card: the QR opens a chat with our number and the JOIN message prefilled
        from . import whatsapp
        url = whatsapp.join_link(token)
    q = qrcode.make(url, border=2).convert("RGB").resize((900, 900))
    img = Image.new("RGB", (1240, 1754), "white")   # A5 at 150 dpi
    img.paste(q, (170, 120))
    d = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
        med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except OSError:
        big = med = ImageFont.load_default()
    y = 1070
    for line, f in [(parent["name"] if parent else "", med), (node["name"], big), ("", med),
                    ("Scan to join on WhatsApp." if via == "wa" else "Scan to join your budget round.", med), ("Propose · vote · approve.", med),
                    ("Money moves only after 60% of members approve.", med), ("", med), (BASE_URL.replace("https://", "").replace("http://", ""), med)]:
        if line:
            d.text((120, y), line, fill="black", font=f)
        y += 70 if f is big else 56
    buf = io.BytesIO(); img.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/j/{token}")
def join(token: str, request: Request):
    """A join link from a poster or the bot: creates/links the member and a pending membership in the node."""
    try:
        t = core.read_token(con, token, "join")
    except core.PBError:
        return page("Link expired", "<p>Ask your coordinator for a fresh link.</p>")
    m = _member(request)
    if not m:
        # web-only join: a bare member; an identity (Telegram, Edge City) gets linked on bot start or sign-in
        mid = db.new_id("m_")
        with db.tx(con):
            con.execute("INSERT INTO members (id,verified_by,created_at) VALUES (?,?,?)", (mid, "deeplink", db.now()))
        m = db.row(con.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone())
    core.join_node(con, t["node_id"], m["id"])
    node = core.get_node(con, t["node_id"])
    body = f"<p>You've asked to join <b>{esc(node['name'])}</b>. The coordinator will confirm you; then you can propose and vote.</p>"
    if BOT_USERNAME:
        body += f"<p><a href='https://t.me/{BOT_USERNAME}?start=j_{esc(token)}'>Open the Telegram bot</a> to get notified when it's your turn.</p>"
    if os.environ.get("PB_WA_NUMBER"):
        from . import whatsapp
        body += f"<p><a href='{esc(whatsapp.join_link(token))}'>Or join on WhatsApp</a>.</p>"
    if EDGEOS_APP_KEY:
        body += "<p class=muted>Or <a href='/signin'>sign in with Edge City</a> to verify you're a resident.</p>"
    return _set_session(page("Welcome", body), m["id"])


@app.get("/l/{token}")
def login_link(token: str, next: str = ""):
    """A one-tap login the bots send: turns a short-lived login token into the signed session cookie.
    `next` is an optional path on this site to land on (a node page, say) instead of /me."""
    try:
        t = core.read_token(con, token, "login", consume=True)
    except core.PBError:
        return page("Link expired", "<p>Ask the bot for a fresh link with /me.</p>")
    dest = next if next.startswith("/") and not next.startswith("//") else "/me"
    return _set_session(RedirectResponse(dest, status_code=303), t["member_id"])


@app.get("/signin")
def signin_form():
    if not EDGEOS_APP_KEY:
        raise HTTPException(404)
    return page("Sign in with Edge City", "<form method=post action=/signin><input name=email type=email placeholder='the email you applied with' required><button>Send me a code</button></form><p class=muted>Edge City emails you a six-digit code. We use your account only to confirm who you are and that you hold a ticket for this popup.</p>")


@app.post("/signin")
async def signin_start(email: str = Form(...)):
    if not EDGEOS_APP_KEY:
        raise HTTPException(404)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{EDGEOS_API_BASE}/auth/human/third-party/login", json={"email": email.strip().lower()},
                         headers={"Authorization": f"Bearer {EDGEOS_APP_KEY}"})
    if r.status_code == 401:
        return page("Not found", "<p>We couldn't find an Edge City account for that email. Use the email you applied with.</p>")
    if r.status_code >= 400:
        return page("Try again", "<p>Couldn't send a code just now.</p>")
    return page("Enter the code", f"<form method=post action=/signin/verify><input type=hidden name=email value='{esc(email)}'><input name=code inputmode=numeric placeholder='6-digit code' required><button>Verify</button></form>")


@app.post("/signin/verify")
async def signin_verify(request: Request, email: str = Form(...), code: str = Form(...)):
    if not EDGEOS_APP_KEY:
        raise HTTPException(404)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{EDGEOS_API_BASE}/auth/human/third-party/verify", json={"email": email.strip().lower(), "code": code.strip()},
                         headers={"Authorization": f"Bearer {EDGEOS_APP_KEY}"})
        if r.status_code >= 400:
            return page("Wrong code", "<p>That code didn't work. <a href=/signin>Try again</a>.</p>")
        pair = r.json()
        token = pair.get("access_token") or pair.get("accessToken")
        me = await c.get(f"{EDGEOS_API_BASE}/humans/me", headers={"Authorization": f"Bearer {token}"})
    if me.status_code >= 400:
        return page("Try again", "<p>Signed in, but couldn't read your profile.</p>")
    prof = me.json()
    human_id = str(prof.get("id") or prof.get("human_id") or "")
    if not human_id:
        return page("Try again", "<p>Edge City didn't return an id.</p>")
    existing = _member(request)
    m = core.upsert_member(con, verified_by="edgeos_login", edgeos_human_id=human_id,
                           telegram_id=existing["telegram_id"] if existing else None,
                           display=(prof.get("first_name") or None))
    # The token is dropped here. It is never stored and never used for anything but /humans/me.
    return _set_session(RedirectResponse("/me", status_code=303), m["id"])


# ----------------------------------------------------------------------------
# resident pages
# ----------------------------------------------------------------------------
@app.get("/me")
def me(request: Request):
    m = _require(request)
    ms = db.rows(con.execute("SELECT ms.*, n.name FROM memberships ms JOIN nodes n ON n.id=ms.node_id WHERE member_id=?", (m["id"],)).fetchall())
    body = f"<p class=muted>Verified by: {esc(m['verified_by'])}</p>"
    for x in ms:
        rnd = core.current_round(con, x["node_id"])
        stage = rnd["status"] if rnd else "no open round"
        body += f"<div class=card><b>{esc(x['name'])}</b> · {esc(x['status'])} · round: {esc(stage)}<br>"
        if x["status"] == "active" and rnd:
            body += f"<a href='/n/{esc(x['node_id'])}'>Open</a>"
        if x["role"] in ("coordinator", "treasurer"):
            body += f" · <a href='/c/{esc(x['node_id'])}'>Coordinate</a>"
        body += "</div>"
    if BOT_USERNAME and not m["telegram_id"]:
        lt = core.mint_token(con, "link", member_id=m["id"])
        body += f"<p><a href='https://t.me/{BOT_USERNAME}?start=l_{esc(lt)}'>Link Telegram</a> to be notified when ballots and approvals open.</p>"
    return page("Your nodes", body)


@app.get("/n/{node_id:path}")
def node_page(node_id: str, request: Request):
    m = _require(request)
    via = core.require_in_tree(con, node_id, m["id"])
    node = core.get_node(con, node_id); rnd = core.current_round(con, node_id)
    kids = core.children(con, node_id)
    if not rnd:
        return page(node["name"], "<p>No open round.</p>")
    props = core.eligible_proposals(con, rnd["id"])
    body = f"<p class=muted>Round {esc(rnd['id'])} · budget {_rupees(core.spendable_budget(con, rnd))} · stage: <b>{esc(rnd['status'])}</b>"
    if kids:
        body += f" · {len(kids)} nodes, contributions in {_rupees(core.contributions_in(con, rnd['id']))}" + (f", matched {node['match_pct']}%" if node.get("match_pct") else "")
        body += f" · {'one vote per node' if node.get('pool_vote') == 'nodes' else 'every member votes'}"
        if via["id"] != node_id: body += f" · you take part through <b>{esc(via['name'])}</b>"
    body += f" · <a href='/b/{esc(node_id)}'>live board</a></p>"
    if rnd["status"] == "proposals":
        signer = kids and via["id"] != node_id and core.is_role(con, via["id"], m["id"], ("coordinator",))
        allp = db.rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND status IN ('eligible','draft') ORDER BY status, created_at", (rnd["id"],)).fetchall())
        names = {k["id"]: k["name"] for k in kids}
        body += "<h2>Proposals so far</h2>"
        for p in allp:
            signs = db.j(p["cosign_nodes"], [])
            body += f"<div class=card><b>{esc(p['title'])}</b> · ideal {_rupees(p['ideal_paise'])}, min {_rupees(p['min_paise'])}"
            if p["status"] == "draft": body += " · <span class=muted>draft" + (": needs a lead" if not p["lead_id"] else "") + (f", {len(signs)} of {node['cosign_min']} nodes" if kids and node.get("cosign_min") else "") + "</span>"
            if p["source"] == "twin": body += " · <span class=muted>suggested from your needs</span>"
            body += f"<br><span class=muted>{esc(p['text'][:200])}</span>"
            if kids and signs: body += "<br><span class=muted>backed by: " + ", ".join(esc(names.get(x, x)) for x in signs) + "</span>"
            acts = ""
            if not p["lead_id"]: acts += f"<form method=post action='/n/{esc(node_id)}/lead' style='display:inline'><input type=hidden name=pid value='{esc(p['id'])}'><button style='width:auto'>I'll lead this</button></form> "
            if signer and via["id"] not in signs: acts += f"<form method=post action='/n/{esc(node_id)}/cosign' style='display:inline'><input type=hidden name=pid value='{esc(p['id'])}'><button style='width:auto'>Co-sign for {esc(via['name'])}</button></form>"
            if acts: body += "<div>" + acts + "</div>"
            body += "</div>"
        body += f"""<h2>Propose something</h2><form method=post action='/n/{esc(node_id)}/propose'>
        <input name=title placeholder='What (e.g. water filter for the hall)' required maxlength=120>
        <textarea name=text placeholder='Who it's for, why, what it needs' rows=3 required></textarea>
        <div class=row><label>Ideal ₹<input name=ideal type=number min=1 required></label><label>Minimum ₹<input name=min type=number min=0 value=0></label></div>
        <input name=upi placeholder='Your UPI ID (only if you lead and will be reimbursed)'>
        <label><input type=checkbox name=lead checked style='width:auto'> I'll lead this if it's funded</label>
        <button>Submit proposal</button></form>"""
    elif rnd["status"] == "ballots":
        mine = db.row(con.execute("SELECT points FROM ballots WHERE round_id=? AND member_id=?", (rnd["id"], m["id"])).fetchone())
        cur = db.j(mine["points"], {}) if mine else {}
        body += f"<h2>Split 100 points</h2><form method=post action='/n/{esc(node_id)}/ballot' id=bf>"
        for p in props:
            body += f"<div class='row'><input name='pt_{esc(p['id'])}' type=number min=0 max=100 value='{cur.get(p['id'], 0)}'><span><b>{esc(p['title'])}</b> <span class=muted>ideal {_rupees(p['ideal_paise'])}, min {_rupees(p['min_paise'])}</span></span></div>"
        body += f"<div class='row'><input name='pt__unspent' type=number min=0 max=100 value='{cur.get('_unspent', 0)}'><span>Leave unspent</span></div>"
        body += "<p class=muted>Must add to 100. You can change it until ballots close.</p><button>Submit ballot</button></form>"
    elif rnd["status"] in ("plans", "ratifying", "ratified", "failed", "paid"):
        plans = core.plans_of(con, rnd["id"]); titles = {p["id"]: p["title"] for p in props}
        rec = core.receipt_for(con, rnd["id"], m["id"])
        if rec:
            tot = sum(rec.values()) or 1
            body += "<h2>Your receipt (Equal Shares)</h2><p>" + ", ".join(f"{esc(titles.get(k, k))} {v * 100 // tot}%" for k, v in sorted(rec.items(), key=lambda kv: -kv[1])) + "</p>"
        body += "<h2>Plans</h2><table><tr><th>Item</th>" + "".join(f"<th>{esc(p['rule'])}</th>" for p in plans) + "</tr>"
        for p in props:
            body += f"<tr><td>{esc(p['title'])}</td>" + "".join(f"<td>{_rupees(db.j(pl['amounts'], {}).get(p['id'], 0))}</td>" for pl in plans) + "</tr>"
        body += "</table>"
        if rnd["status"] == "plans":
            body += f"<h2>Rank the plans</h2><form method=post action='/n/{esc(node_id)}/rank'><p class=muted>Best first.</p>"
            for i in range(len(plans)):
                body += f"<select name='r{i}'>" + "".join(f"<option value='{esc(pl['id'])}'>{esc(pl['rule'])}</option>" for pl in plans) + "</select>"
            body += "<button>Submit ranking</button></form>"
        if rnd["status"] == "ratifying":
            body += f"<h2>Approve the winning plan ({esc(next((p['rule'] for p in plans if p['id']==rnd['winner_plan_id']), '?'))})?</h2><form method=post action='/n/{esc(node_id)}/approve'><button name=verdict value=approve>Approve</button><button name=verdict value=reject style='background:#b00'>Reject</button></form><p class=muted>{'Funding happens only when every node in the pool approves at its own threshold.' if kids else f"Funding happens only if at least {node['ratify_pct']}% of all members approve."}</p>"
        if rnd["status"] in ("ratified", "paid"):
            d = db.row(con.execute("SELECT * FROM decisions WHERE round_id=? ORDER BY decided_at DESC LIMIT 1", (rnd["id"],)).fetchone())
            body += f"<p class=ok>Ratified: {d['approvals']} of {d['eligible']} approved.</p>" if d else ""
            q = db.rows(con.execute("SELECT p.status, p.amount_paise, pr.title, p.reference FROM payouts p JOIN proposals pr ON pr.id=p.proposal_id WHERE p.round_id=?", (rnd["id"],)).fetchall())
            body += "<h2>Ledger</h2><table><tr><th>Item</th><th>Amount</th><th>Status</th><th>Ref</th></tr>" + "".join(f"<tr><td>{esc(x['title'])}</td><td>{_rupees(x['amount_paise'])}</td><td>{esc(x['status'])}</td><td>{esc(x['reference'] or '')}</td></tr>" for x in q) + "</table>"
        if rnd["status"] == "failed":
            body += "<p class=bad>The plan did not reach the approval threshold. No money moves.</p>"
    return page(node["name"], body)


@app.post("/n/{node_id:path}/propose")
def propose(node_id: str, request: Request, title: str = Form(...), text: str = Form(...), ideal: int = Form(...), min: int = Form(0), upi: str = Form(""), lead: str | None = Form(None)):
    m = _require(request); rnd = core.current_round(con, node_id)
    if not rnd: raise HTTPException(400, "no open round")
    if upi.strip():
        with db.tx(con): con.execute("UPDATE members SET upi_id=? WHERE id=?", (upi.strip(), m["id"]))
    try:
        core.add_proposal(con, rnd["id"], m["id"], title=title, text=text, ideal_paise=ideal * 100, min_paise=min * 100, lead_id=m["id"] if lead else None)
    except core.PBError as e:
        raise HTTPException(400, e.code)
    return RedirectResponse(f"/n/{node_id}", status_code=303)


@app.post("/n/{node_id:path}/lead")
def lead(node_id: str, request: Request, pid: str = Form(...)):
    m = _require(request)
    try: core.lead_proposal(con, pid, m["id"])
    except core.PBError as e: raise HTTPException(400, e.code)
    return RedirectResponse(f"/n/{node_id}", status_code=303)


@app.post("/n/{node_id:path}/cosign")
def cosign(node_id: str, request: Request, pid: str = Form(...)):
    m = _require(request)
    via = core.require_in_tree(con, node_id, m["id"])
    try: core.cosign_proposal(con, pid, via["id"], m["id"])
    except core.PBError as e: raise HTTPException(400, e.code)
    return RedirectResponse(f"/n/{node_id}", status_code=303)


@app.post("/n/{node_id:path}/ballot")
async def ballot(node_id: str, request: Request):
    m = _require(request); rnd = core.current_round(con, node_id)
    form = await request.form()
    pts = {k[3:]: int(v or 0) for k, v in form.items() if k.startswith("pt_")}
    try:
        core.submit_ballot(con, rnd["id"], m["id"], pts)
    except core.PBError as e:
        return page("Ballot not saved", f"<p class=bad>{esc(e.code)} {esc(e.detail)}</p><p><a href='/n/{esc(node_id)}'>Back</a></p>")
    return RedirectResponse(f"/n/{node_id}", status_code=303)


@app.post("/n/{node_id:path}/rank")
async def rank(node_id: str, request: Request):
    m = _require(request); rnd = core.current_round(con, node_id)
    form = await request.form()
    order = [form[f"r{i}"] for i in range(len(core.plans_of(con, rnd["id"]))) if f"r{i}" in form]
    seen = []; [seen.append(x) for x in order if x not in seen]
    rest = [p["id"] for p in core.plans_of(con, rnd["id"]) if p["id"] not in seen]
    try:
        core.submit_ranking(con, rnd["id"], m["id"], seen + rest)
    except core.PBError as e:
        raise HTTPException(400, e.code)
    return RedirectResponse(f"/n/{node_id}", status_code=303)


@app.post("/n/{node_id:path}/approve")
def approve(node_id: str, request: Request, verdict: str = Form(...)):
    m = _require(request); rnd = core.current_round(con, node_id)
    plan_id = rnd["winner_plan_id"]
    if not plan_id:
        raise HTTPException(400, "no winner yet")
    try:
        core.submit_ratification(con, rnd["id"], m["id"], plan_id, verdict)
    except core.PBError as e:
        raise HTTPException(400, e.code)
    return RedirectResponse(f"/n/{node_id}", status_code=303)


# ----------------------------------------------------------------------------
# coordinator and treasurer
# ----------------------------------------------------------------------------
@app.get("/c/{node_id:path}")
def coordinate(node_id: str, request: Request):
    m = _require(request)
    if not core.is_role(con, node_id, m["id"], ("coordinator", "treasurer")): raise HTTPException(403)
    pend = db.rows(con.execute("SELECT m.id, m.display, m.verified_by, ms.status FROM memberships ms JOIN members m ON m.id=ms.member_id WHERE ms.node_id=? ORDER BY ms.status, ms.joined_at", (node_id,)).fetchall())
    rnd = core.current_round(con, node_id)
    body = "<h2>Members</h2><table><tr><th>Who</th><th>Verified by</th><th>Status</th><th></th></tr>"
    for p in pend:
        act = f"<form method=post action='/c/{esc(node_id)}/approve-member' style='margin:0'><input type=hidden name=member_id value='{esc(p['id'])}'><button>Approve</button></form>" if p["status"] == "pending" else ""
        body += f"<tr><td>{esc(p['display'] or p['id'][:8])}</td><td>{esc(p['verified_by'])}</td><td>{esc(p['status'])}</td><td>{act}</td></tr>"
    body += "</table>"
    if rnd:
        nxt = {"proposals": ("freeze", "Freeze proposals → open ballots"), "ballots": ("compute", "Close ballots → compute plans"),
               "plans": ("winner", "Close ranking → pick winner, open approval"), "ratifying": ("close", "Close approval → decide")}.get(rnd["status"])
        body += f"<h2>Round {esc(rnd['id'])}: {esc(rnd['status'])}</h2>"
        if nxt:
            body += f"<form method=post action='/c/{esc(node_id)}/advance'><input type=hidden name=step value='{nxt[0]}'><button>{nxt[1]}</button></form>"
    tok = core.mint_token(con, "join", node_id=node_id, ttl=core.TOKEN_TTL_JOIN)
    base = str(request.base_url).rstrip("/")
    body += f"<h2>Join link (put this on the poster)</h2><p><code>{esc(base)}/j/{esc(tok)}</code></p>"
    if BOT_USERNAME: body += f"<p><code>https://t.me/{BOT_USERNAME}?start=j_{esc(tok)}</code></p>"
    if os.environ.get("PB_WA_NUMBER"):
        from . import whatsapp
        body += f"<p>WhatsApp: <code>{esc(whatsapp.join_link(tok))}</code> · <a href='/j/{esc(tok)}/poster.png?via=wa'>card</a></p>"
    return page(f"Coordinate · {core.get_node(con, node_id)['name']}", body)


@app.post("/c/{node_id:path}/approve-member")
def approve_member(node_id: str, request: Request, member_id: str = Form(...)):
    m = _require(request)
    try: core.approve_member(con, node_id, member_id, m["id"])
    except core.PBError as e: raise HTTPException(403, e.code)
    return RedirectResponse(f"/c/{node_id}", status_code=303)


@app.post("/c/{node_id:path}/advance")
def advance(node_id: str, request: Request, step: str = Form("")):
    m = _require(request)
    if not core.is_role(con, node_id, m["id"], ("coordinator", "treasurer")): raise HTTPException(403)
    rnd = core.current_round(con, node_id)
    if not rnd: raise HTTPException(400, "no_open_round")
    try:
        done = core.advance(con, rnd["id"])
    except core.PBError as e:
        raise HTTPException(400, e.code)
    _announce(node_id, done)
    return RedirectResponse(f"/c/{node_id}", status_code=303)


# (template, Telegram command, WhatsApp keyword); {cmd} becomes "Send /x here" or "Reply X here"
ANNOUNCE = {
    "freeze": ("Proposals are frozen in {node}. Ballots are open: split 100 points across the items. {cmd}\n{url}", "/ballot", "VOTE"),
    "compute": ("The plans for {node} are ready. See where your equal share went and rank the candidates. {cmd}\n{url}", "/rank", "RANK"),
    "winner": ("A winning plan was picked for {node}. Please approve or reject it; 60% of members must approve before any money moves. {cmd}\n{url}", "/approve", "APPROVE"),
    "close": ("The round in {node} is decided. Open the page for the result.{cmd}\n{url}", "", ""),
}


def _announce(node_id: str, step: str) -> None:
    """Fire-and-forget DM to every linked member on Telegram and WhatsApp; a missing bot or a send error never
    blocks the coordinator."""
    if step not in ANNOUNCE or not (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("PB_WA_TOKEN")):
        return
    tmpl, tg_cmd, wa_cmd = ANNOUNCE[step]
    node = core.get_node(con, node_id)
    url = f"{BASE_URL}/n/{node_id}"
    tg_text = tmpl.format(node=node["name"], url=url, cmd=f"Send {tg_cmd} here." if tg_cmd else "")
    wa_text = tmpl.format(node=node["name"], url=url, cmd=f"Reply {wa_cmd} here." if wa_cmd else "")
    import threading
    threading.Thread(target=_send_all, args=(node_id, tg_text, wa_text), daemon=True).start()


def _send_all(node_id: str, tg_text: str, wa_text: str) -> None:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if tok:
        for mem in core.active_members(con, node_id):
            if not mem["telegram_id"]:
                continue
            try:
                httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": mem["telegram_id"], "text": tg_text}, timeout=10)
            except Exception:
                pass
    if os.environ.get("PB_WA_TOKEN"):
        from . import whatsapp
        try:
            whatsapp.notify_node(node_id, wa_text)
        except Exception:
            pass


@app.get("/treasury")
def treasury(request: Request):
    m = _require(request)
    if not db.row(con.execute("SELECT 1 FROM memberships WHERE member_id=? AND role='treasurer' AND status='active'", (m["id"],)).fetchone()): raise HTTPException(403)
    q = core.payout_queue(con)
    body = "<table><tr><th>Round</th><th>Item</th><th>Amount</th><th>Pay to</th><th></th></tr>"
    for x in q:
        pa = x["payee_upi"] or x["lead_upi"] or ""
        link = core.upi_link(pa, x["amount_paise"], f"{x['round_id']} {x['title']}") if pa else ""
        body += f"<tr><td>{esc(x['round_id'])}</td><td>{esc(x['title'])}</td><td>{_rupees(x['amount_paise'])}</td><td>{esc(pa) or '<i>no UPI yet</i>'}</td><td>{'<a href=\"' + esc(link) + '\">Pay in UPI app</a>' if link else ''}<form method=post action='/treasury/paid' style='margin:0'><input type=hidden name=payout_id value='{esc(x['id'])}'><input name=reference placeholder='UPI/IMPS ref' required><button>Paid</button></form></td></tr>"
    body += "</table><p class=muted>Each payout is a purchase or a reimbursement against a receipt. Put the round id in the UPI remark.</p>"
    return page("Treasury · payout queue", body)


@app.post("/treasury/paid")
def paid(request: Request, payout_id: str = Form(...), reference: str = Form(...)):
    m = _require(request)
    if not db.row(con.execute("SELECT 1 FROM memberships WHERE member_id=? AND role='treasurer' AND status='active'", (m["id"],)).fetchone()): raise HTTPException(403)
    core.mark_paid(con, payout_id, reference)
    return RedirectResponse("/treasury", status_code=303)


# ----------------------------------------------------------------------------
# the live board: one public, read-only page per node, for a projector or a phone
# ----------------------------------------------------------------------------
BOARD_CSS = "<style>body{max-width:960px;font-size:18px}h1{font-size:30px}h2{font-size:21px}td,th{font-size:16px}.big{font-size:40px;font-weight:700}.stage{display:inline-block;padding:4px 12px;border-radius:999px;background:#0b6e4f;color:#fff;font-size:15px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:700px){.grid{grid-template-columns:1fr}}</style>"
STAGE_WORDS = {"proposals": "Proposals open", "ballots": "Voting", "plans": "Plans ready, ranking", "ratifying": "Approval",
               "ratified": "Approved", "failed": "Not approved", "paid": "Paid out", "closed": "Closed"}


@app.get("/b/{node_id:path}")
def board(node_id: str):
    """Everything the room may see. No names, no ids, no UPI handles; the payer never appears."""
    try:
        node = core.get_node(con, node_id)
    except core.PBError:
        raise HTTPException(404)
    kids = core.children(con, node_id)
    rnd = core.current_round(con, node_id)
    names = {k["id"]: k["name"] for k in kids}
    body = ""
    if node.get("parent_id"):
        par = core.get_node(con, node["parent_id"])
        body += f"<p class=muted>Part of <a href='/b/{esc(par['id'])}'>{esc(par['name'])}</a></p>"
    if not rnd:
        body += "<p class=big>No round open</p>"
    else:
        st = rnd["status"]
        body += f"<p><span class=stage>{esc(STAGE_WORDS.get(st, st))}</span> &nbsp; <span class=big>{_rupees(core.spendable_budget(con, rnd))}</span>"
        if kids:
            inn = core.contributions_in(con, rnd["id"])
            body += f" <span class=muted>= {_rupees(inn)} from {len({c['from_node_id'] for c in db.rows(con.execute('SELECT from_node_id FROM contributions WHERE to_round_id=?', (rnd['id'],)).fetchall())})} nodes"
            body += f" + match {node['match_pct']}%" if node.get("match_pct") else ""
            body += "</span>"
        body += "</p>"
    if kids:
        contributed = {}
        if rnd:
            for c in db.rows(con.execute("SELECT from_node_id, SUM(amount_paise) AS a FROM contributions WHERE to_round_id=? GROUP BY from_node_id", (rnd["id"],)).fetchall()):
                contributed[c["from_node_id"]] = c["a"]
        body += f"<h2>{len(kids)} nodes · {'one vote each' if node.get('pool_vote') == 'nodes' else 'every member votes'}</h2><table><tr><th>Node</th><th>Members</th><th>In the pool</th><th>Contributed</th></tr>"
        for k in kids:
            body += f"<tr><td>{esc(k['name'])}</td><td>{len(core.active_members(con, k['id']))}</td><td>{'yes' if k.get('charter_at') else '—'}</td><td>{_rupees(contributed[k['id']]) if k['id'] in contributed else '—'}</td></tr>"
        body += "</table>"
    needs = core.needs_of(con, node_id) if (not rnd or rnd["status"] == "proposals") else []
    if needs:
        body += "<h2>What people said they need</h2>" + "".join(f"<div class=card>{esc(n['text'])} <span class=muted>· {esc(n['node_name'])}</span></div>" for n in needs[:30])
    if rnd:
        st = rnd["status"]
        allp = db.rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND status IN ('eligible','draft') ORDER BY status, created_at", (rnd["id"],)).fetchall())
        if allp:
            body += "<h2>Proposals</h2>"
            for p in allp:
                signs = db.j(p["cosign_nodes"], [])
                body += f"<div class=card><b>{esc(p['title'])}</b> · ideal {_rupees(p['ideal_paise'])}, min {_rupees(p['min_paise'])}"
                if p["status"] == "draft": body += " · <span class=muted>draft" + (", needs a lead" if not p["lead_id"] else "") + "</span>"
                if p["source"] == "twin": body += " · <span class=muted>suggested from needs</span>"
                if kids and signs: body += "<br><span class=muted>backed by " + ", ".join(esc(names.get(x, x)) for x in signs) + "</span>"
                body += f"<br><span class=muted>{esc(p['text'][:240])}</span></div>"
        if st in ("ballots",):
            body += f"<h2>Ballots</h2><p class=big>{len(core.ballots_of(con, rnd['id']))} of {len(core.voters(con, rnd))}</p>"
        if st in ("plans", "ratifying", "ratified", "failed", "paid"):
            plans = core.plans_of(con, rnd["id"]); props = core.eligible_proposals(con, rnd["id"])
            rule_names = {"mes": "Equal Shares", "nash": "Nash", "utilitarian": "Most points", "maximin": "Least served"}
            body += f"<h2>Plans</h2><p class=muted>{len(core.ballots_of(con, rnd['id']))} ballots" + (f" · {db.row(con.execute('SELECT COUNT(*) AS c FROM rankings WHERE round_id=?', (rnd['id'],)).fetchone())['c']} rankings" if st == "plans" else "") + "</p>"
            body += "<table><tr><th>Item</th>" + "".join(f"<th>{esc(rule_names.get(p['rule'], p['rule']))}{' ★' if p['id'] == rnd['winner_plan_id'] else ''}</th>" for p in plans) + "</tr>"
            for p in props:
                body += f"<tr><td>{esc(p['title'])}</td>" + "".join(f"<td>{_rupees(db.j(pl['amounts'], {}).get(p['id'], 0))}</td>" for pl in plans) + "</tr>"
            body += "</table>"
        if st == "ratifying":
            t = core.ratification_tally(con, rnd["id"], rnd["winner_plan_id"])
            body += f"<h2>Approval</h2><p class=big>{t['approvals']} of {t['eligible']}</p>"
            if t["detail"]:
                body += "<table><tr><th>Node</th><th>Approved</th><th>Of</th><th>Needs</th><th></th></tr>" + "".join(
                    f"<tr><td>{esc(d['name'])}</td><td>{d['approvals']}</td><td>{d['eligible']}</td><td>{d['pct']}%</td><td>{'✓' if d['ok'] else '…'}</td></tr>" for d in t["detail"].values()) + "</table>"
            else:
                body += f"<p class=muted>Needs {t['pct']}% of {t['eligible']} to approve. Not answering is not approving.</p>"
        if st in ("ratified", "failed", "paid"):
            d = db.row(con.execute("SELECT * FROM decisions WHERE round_id=? ORDER BY decided_at DESC LIMIT 1", (rnd["id"],)).fetchone())
            if d:
                body += f"<h2>Decision</h2><p class='{'ok' if d['verdict'] == 'ratified' else 'bad'} big'>{esc(d['verdict'])}</p><p class=muted>{d['approvals']} of {d['eligible']} approved · {esc(d['rule'])}</p>"
                det = db.j(d.get("detail"), {}) or {}
                if det:
                    body += "<table><tr><th>Node</th><th>Approved</th><th>Of</th><th></th></tr>" + "".join(f"<tr><td>{esc(v['name'])}</td><td>{v['approvals']}</td><td>{v['eligible']}</td><td>{'✓' if v['ok'] else '✗'}</td></tr>" for v in det.values()) + "</table>"
            q = db.rows(con.execute("SELECT p.status, p.amount_paise, p.reference, pr.title FROM payouts p JOIN proposals pr ON pr.id=p.proposal_id WHERE p.round_id=? ORDER BY p.created_at", (rnd["id"],)).fetchall())
            if q:
                body += "<h2>Ledger</h2><table><tr><th>Item</th><th>Amount</th><th>Status</th><th>Reference</th></tr>" + "".join(f"<tr><td>{esc(x['title'])}</td><td>{_rupees(x['amount_paise'])}</td><td>{esc(x['status'])}</td><td>{esc(x['reference'] or '')}</td></tr>" for x in q) + "</table>"
    body += f"<p class=muted><a href='/export/{esc(node_id)}'>Export</a> · the record anyone can check. No names, no account details.</p>"
    html = page(node["name"], body).body.decode()
    return HTMLResponse(html.replace("<title>", "<meta http-equiv=refresh content=15>" + BOARD_CSS + "<title>", 1))


# ----------------------------------------------------------------------------
# public export and health
# ----------------------------------------------------------------------------
@app.get("/export/{node_id:path}")
def export(node_id: str):
    try:
        return JSONResponse(core.export_node(con, node_id))
    except core.PBError:
        raise HTTPException(404)


@app.get("/health")
def health():
    return {"ok": True}
