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
import re
import urllib.parse
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


def _back(next: str, default: str) -> str:
    return next if next.startswith("/") and not next.startswith("//") else default


def _rupees(p: int) -> str:
    from .convo import rupees
    return rupees(p)


# ----------------------------------------------------------------------------
# minimal HTML
# ----------------------------------------------------------------------------
FONTS = ('<link rel=preconnect href=https://fonts.googleapis.com><link rel=preconnect href=https://fonts.gstatic.com crossorigin>'
         '<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Newsreader:ital,wght@0,300;0,400;0,500;1,400&family=IBM+Plex+Mono:wght@400;500&family=Silkscreen&display=swap">')
# The look of cyberneticsutra.com: paper, ink, Newsreader for words, Silkscreen for buttons, Plex Mono for numbers,
# 2px ink borders with hard offset shadows. Light only, like the site.
CSS = """<style>:root{--paper:#f3e9d6;--card:#fbf6e9;--ink:#20201d;--text:#534e41;--muted:#7a7465;--gold:#d9a33d;--line:#20201d33;--shadow:#20201d59}
body{font:300 18px/1.6 Newsreader,Georgia,serif;background:var(--paper);color:var(--text);max-width:680px;margin:0 auto;padding:28px 20px 72px}
h1{font:400 44px/1.05 Newsreader,Georgia,serif;letter-spacing:-.025em;color:var(--ink);margin:10px 0 18px}
h2{font:400 26px/1.15 Newsreader,Georgia,serif;letter-spacing:-.015em;color:var(--ink);margin:36px 0 10px}
.brand{font:400 10px/1 Silkscreen,"IBM Plex Mono",monospace;text-transform:uppercase;color:var(--ink);margin:0 0 6px}.brand a{text-decoration:none;color:inherit}
a{color:var(--ink)}b,strong{font-weight:500;color:var(--ink)}p{margin:10px 0}
.card{background:var(--card);border:2px solid var(--ink);border-radius:3px;box-shadow:6px 6px 0 #20201dcc;padding:16px 18px;margin:20px 0}
label{display:block;margin-top:20px;font-weight:500;color:var(--ink)}.hint{display:block;font-weight:300;color:var(--muted);font-size:16px;line-height:1.4}
input,textarea,select{font:400 17px "IBM Plex Mono",monospace;padding:11px 12px;border:2px solid var(--ink);border-radius:3px;width:100%;box-sizing:border-box;margin:8px 0 0;background:var(--card);color:var(--ink)}
input:focus,textarea:focus,select:focus{outline:3px solid var(--gold);outline-offset:1px}
button{font:400 12px/1 Silkscreen,"IBM Plex Mono",monospace;text-transform:uppercase;background:var(--card);color:var(--ink);border:2px solid var(--ink);border-radius:10px;padding:15px 22px;box-shadow:3px 3px 0 var(--shadow);cursor:pointer;width:100%;box-sizing:border-box;margin-top:22px}
button:active{transform:translate(2px,2px);box-shadow:1px 1px 0 var(--shadow)}button.quiet{box-shadow:none;margin-top:8px}
.choice{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}.choice label{flex:1 1 0;box-sizing:border-box;margin:0;border:2px solid var(--ink);border-radius:10px;padding:13px 6px;text-align:center;background:var(--card);box-shadow:3px 3px 0 var(--shadow);font:400 11px/1 Silkscreen,"IBM Plex Mono",monospace;text-transform:uppercase;white-space:nowrap;cursor:pointer}
.choice input{position:absolute;opacity:0;width:0;height:0}.choice label:has(input:checked){background:var(--gold)}.choice.multi label{flex:1 1 30%;white-space:normal;line-height:1.3}
.muted{color:var(--muted);font-size:16px}.row{display:flex;gap:8px;align-items:center}.row input{width:90px}
code{font:400 14px "IBM Plex Mono",monospace;background:var(--card);border:1px solid var(--line);padding:2px 6px;border-radius:3px}
table{width:100%;border-collapse:collapse;font:400 14px/1.4 "IBM Plex Mono",monospace;color:var(--ink)}td,th{padding:8px 6px;border-top:1px solid var(--line);text-align:left;vertical-align:top}th{font-weight:500;color:var(--muted)}
.consent{display:flex;gap:14px;align-items:flex-start;margin-top:24px;padding:14px 16px;border:2px solid var(--ink);border-radius:3px;background:var(--card);box-shadow:4px 4px 0 var(--shadow);font-weight:300;cursor:pointer}.consent input{width:26px;height:26px;margin:2px 0 0;flex:0 0 auto;accent-color:var(--ink)}.consent .hint{margin-top:4px}
.phone{display:flex;gap:8px}.phone .cc{position:relative;width:38%;flex:0 0 auto;margin:8px 0 0}.phone .cc select{width:100%;margin:0;color:transparent;padding:11px 4px 11px 8px}.phone .cc select option{color:var(--ink)}.phone .ccv{position:absolute;left:12px;top:50%;transform:translateY(-50%);pointer-events:none;font:400 17px 'IBM Plex Mono',monospace;color:var(--ink);white-space:nowrap}.phone input{flex:1 1 auto;min-width:0}
.bad{color:#9b2c1f}.ok{color:#2f6b3f}.hp{position:absolute;left:-9999px}.rules{padding-left:22px;margin:6px 0 4px;color:var(--ink)}.rules li{margin:6px 0}.drafting{color:var(--muted)}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--gold);margin-right:8px;animation:pulse 1s ease-in-out infinite}@keyframes pulse{50%{opacity:.3}}.confetti{position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:0}.confetti i{position:absolute;top:-14px;width:10px;height:16px;border-radius:2px;opacity:0;animation:fall linear forwards}@keyframes fall{0%{opacity:1;transform:translateY(0) rotate(0)}100%{opacity:0;transform:translateY(105vh) rotate(540deg)}}.tick.big{width:96px;height:96px;margin:10px auto 14px}.tick.big svg{width:96px;height:96px}.lead{font-size:24px;text-align:center;color:var(--ink)}@media(prefers-reduced-motion:reduce){.confetti{display:none}}
button.small{width:auto;padding:8px 12px;margin:0 8px 0 0;font-size:10px}.prop{border-top:1px solid var(--line);padding:12px 0}.tag{display:inline-block;font:400 9px/1 Silkscreen,monospace;text-transform:uppercase;background:var(--gold);color:var(--ink);padding:5px 7px;border-radius:8px;vertical-align:middle;margin-right:4px}.prop .row{margin-top:8px;flex-wrap:wrap}.prop form{margin:0}.prop.did{animation:flash .9s ease-out}@keyframes flash{0%{background:#d9a33d55}100%{background:transparent}}.tag.vote{background:#2f6b3f;color:#fbf6e9}details summary{cursor:pointer;font-weight:500;color:var(--ink);margin-top:10px}.propose{margin:4px 0 18px}.propose summary{list-style:none;display:inline-block;text-align:center;font:400 12px/1 Silkscreen,'IBM Plex Mono',monospace;text-transform:uppercase;background:var(--card);color:var(--ink);border:2px solid var(--ink);border-radius:10px;padding:13px 20px;box-shadow:3px 3px 0 var(--shadow);margin-top:0}.propose summary::-webkit-details-marker{display:none}.propose[open] summary{box-shadow:1px 1px 0 var(--shadow);transform:translate(2px,2px)}a.btn{display:block;text-align:center;font:400 12px/1 Silkscreen,"IBM Plex Mono",monospace;text-transform:uppercase;background:var(--card);color:var(--ink);border:2px solid var(--ink);border-radius:10px;padding:15px 22px;box-shadow:3px 3px 0 var(--shadow);text-decoration:none;margin-top:14px}
.steps{display:flex;gap:6px;list-style:none;padding:0;margin:0 0 22px;font:400 10px/1 Silkscreen,"IBM Plex Mono",monospace;text-transform:uppercase;color:var(--muted)}.steps li{flex:1;border-top:3px solid var(--line);padding-top:8px}.steps li span{display:none}.steps li.done{border-color:var(--ink);color:var(--ink)}.steps li.now{border-color:var(--gold);color:var(--ink)}
.slider{display:flex;align-items:center;gap:10px;margin-top:10px}.slider .lo,.slider .hi{font-size:13px;color:var(--muted);flex:0 0 auto}.slider output{font:500 18px "IBM Plex Mono",monospace;min-width:2ch;text-align:right;color:var(--ink)}
.slider input{flex:1;margin:0;padding:0;border:0;background:transparent;-webkit-appearance:none;appearance:none;height:28px;--a:50%;--b:50%}.poles{display:flex;justify-content:space-between;font-weight:500;color:var(--ink)}.poleword{text-align:center;margin:2px 0 6px}.poleword output{font:400 10px/1 Silkscreen,'IBM Plex Mono',monospace;text-transform:uppercase;color:var(--ink);background:var(--gold);padding:6px 10px;border-radius:8px;display:inline-block;min-width:6ch}.axis .hint{margin-top:6px}
.slider input::-webkit-slider-runnable-track{height:6px;border-radius:3px;background:linear-gradient(90deg,var(--line) var(--a),var(--gold) var(--a),var(--gold) var(--b),var(--line) var(--b))}.slider input::-moz-range-track{height:6px;border-radius:3px;background:var(--line)}.slider input::-moz-range-progress{height:6px;border-radius:3px;background:var(--gold)}
.slider input::-webkit-slider-thumb{-webkit-appearance:none;width:26px;height:26px;margin-top:-10px;border-radius:50%;background:var(--card);border:2px solid var(--ink);box-shadow:2px 2px 0 var(--shadow);cursor:pointer;transition:transform .12s}.slider input::-webkit-slider-thumb:active{transform:scale(1.15)}.slider input::-moz-range-thumb{width:22px;height:22px;border-radius:50%;background:var(--card);border:2px solid var(--ink);box-shadow:2px 2px 0 var(--shadow)}
.tick{width:64px;height:64px;margin:4px 0 12px}.tick svg{width:64px;height:64px;stroke:var(--ink);stroke-width:3;stroke-linecap:round;stroke-linejoin:round}.tick circle{stroke-dasharray:160;stroke-dashoffset:160;animation:draw .7s ease-out forwards}.tick path{stroke:#2f6b3f;stroke-dasharray:40;stroke-dashoffset:40;animation:draw .5s .5s ease-out forwards}
@keyframes draw{to{stroke-dashoffset:0}}@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.rise{animation:rise .45s ease-out both}.rise:nth-of-type(2){animation-delay:.08s}.rise:nth-of-type(3){animation-delay:.16s}.rise:nth-of-type(4){animation-delay:.24s}.rise:nth-of-type(5){animation-delay:.32s}
h1{animation:rise .4s ease-out both}.choice label,button{transition:transform .12s,box-shadow .12s,background .2s}.choice label:active{transform:translate(2px,2px);box-shadow:1px 1px 0 var(--shadow)}
@media(prefers-reduced-motion:reduce){.rise,h1,.tick circle,.tick path{animation:none;opacity:1;stroke-dashoffset:0}}</style>"""


BRAND = os.environ.get("PB_BRAND", "The Cybernetic Society")
LIVE_STAGES = ("proposals", "ballots", "plans", "ratifying")   # a round someone can still act in


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{title}</title>{FONTS}{CSS}"
                        f"<p class=brand><a href='/'>{esc(BRAND)}</a></p><h1>{title}</h1>{body}")


def esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ----------------------------------------------------------------------------
# join and sign-in
# ----------------------------------------------------------------------------
@app.get("/j/{token}/poster.png")
def poster(token: str, via: str = ""):
    """A printable A5-ish poster for a join link: QR + node name. Public, but the token is the secret."""
    t = None
    for kind in ("join", "join_coord", "join_team"):
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
                    ("The fund pays the vendor once the plan is approved.", med), ("", med), (BASE_URL.replace("https://", "").replace("http://", ""), med)]:
        if line:
            d.text((120, y), line, fill="black", font=f)
        y += 70 if f is big else 56
    buf = io.BytesIO(); img.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/j/{token}")
def join(token: str, request: Request):
    """A join link from a poster or the bot: creates/links the member and a pending membership in the node."""
    t = None
    for kind in ("join", "join_coord", "join_team"):
        try:
            t = core.read_token(con, token, kind); break
        except core.PBError:
            pass
    if not t:
        return page("Link expired", "<p>Ask your coordinator for a fresh link.</p>")
    m = _member(request)
    if not m:
        # web-only join: a bare member; an identity (Telegram, Edge City) gets linked on bot start or sign-in
        mid = db.new_id("m_")
        with db.tx(con):
            con.execute("INSERT INTO members (id,verified_by,created_at) VALUES (?,?,?)", (mid, "deeplink", db.now()))
        m = db.row(con.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone())
    node = core.get_node(con, t["node_id"])
    if t["kind"] == "join_coord":
        # an owner's card: whoever opens it runs this business. The card went to the phone the business gave us.
        core.join_node(con, node["id"], m["id"], role="coordinator", auto_active=True)
        core.claim_application(con, node["id"], m["id"])
        return _set_session(_coordinator_welcome(node, m, request), m["id"])
    if core.is_role(con, node["id"], m["id"], ("coordinator", "treasurer")):
        return _set_session(_coordinator_welcome(node, m, request), m["id"])
    if t["kind"] == "join_team":   # the owner handed this link out themselves: in at once
        core.join_node(con, t["node_id"], m["id"], auto_active=True)
        body = f"<p>You're in: <b>{esc(node['name'])}</b>. When there's something to propose, vote on or approve, it's at <a href='/n/{esc(node['id'])}'>your node's page</a>.</p>"
        return _set_session(page("Welcome", body), m["id"])
    core.join_node(con, t["node_id"], m["id"])
    body = f"<p>You've asked to join <b>{esc(node['name'])}</b>. The coordinator will confirm you; then you can propose and vote.</p>"
    if BOT_USERNAME:
        body += f"<p><a href='https://t.me/{BOT_USERNAME}?start=j_{esc(token)}'>Open the Telegram bot</a> to get notified when it's your turn.</p>"
    if os.environ.get("PB_WA_NUMBER"):
        from . import whatsapp
        body += f"<p><a href='{esc(whatsapp.join_link(token))}'>Or join on WhatsApp</a>.</p>"
    if EDGEOS_APP_KEY:
        body += "<p class=muted>Or <a href='/signin'>sign in with Edge City</a> to verify you're a resident.</p>"
    return _set_session(page("Welcome", body), m["id"])


def _coordinator_welcome(node: dict, m: dict, request: Request) -> HTMLResponse:
    """What an owner sees after opening their card: their team link, the pool's charter to accept, their pages."""
    from . import convo
    base = str(request.base_url).rstrip("/")
    tok = core.mint_token(con, "join_team", node_id=node["id"], ttl=core.TOKEN_TTL_JOIN)
    body = f"<p>You run <b>{esc(node['name'])}</b> here: you decide who is in, and you cast its vote.</p>"
    parent = core.get_node(con, node["parent_id"]) if node.get("parent_id") else None
    if parent and not node.get("charter_at"):
        body += (f"<div class=card><h2 style='margin-top:0'>{esc(parent['name'])} works like this</h2><ol class=rules>" + "".join(f"<li>{esc(ln)}</li>" for ln in convo.CHARTER_LINES) + "</ol>"
                 f"<form method=post action='/n/{esc(node['id'])}/charter'><button>Agree and continue</button></form></div>")
    elif parent:
        body += f"<p class=ok>{esc(node['name'])} is in {esc(parent['name'])}.</p>"
    link = f"{base}/j/{tok}"
    if os.environ.get("PB_WA_NUMBER"):
        from . import whatsapp
        link = whatsapp.join_link(tok)
    body += (f"<h2>Your team</h2><p>Share this with the people who work with you, so they can vote too. Anyone who opens it joins "
             f"{esc(node['name'])} straight away. It works for a week.</p><p><code style='word-break:break-all'>{esc(link)}</code></p>"
             f"<p><a href='https://wa.me/?text={esc(urllib.parse.quote(f'Join {node['name']} on {parent['name'] if parent else BRAND}: {link}'))}'>Send it on WhatsApp</a></p>")
    body += f"<h2>Next</h2><p><a href='/me'>Your nodes</a> · <a href='/c/{esc(node['id'])}'>Coordinate</a>" + (f" · <a href='/b/{esc(parent['id'])}'>Live board</a>" if parent else "") + "</p>"
    return page(f"Welcome, {esc(m.get('display') or 'coordinator')}", body)


@app.post("/n/{node_id:path}/charter")
def accept_charter(node_id: str, request: Request, next: str = ""):
    m = _require(request)
    if not core.is_role(con, node_id, m["id"], ("coordinator",)): raise HTTPException(403)
    with db.tx(con):
        con.execute("UPDATE nodes SET charter_at=COALESCE(charter_at,?) WHERE id=?", (db.now(), node_id))
    node = core.get_node(con, node_id)
    if node.get("parent_id"):
        from . import twin
        twin.schedule(node["parent_id"])   # another business is in: see whether needs now line up
    dest = next if next.startswith("/") and not next.startswith("//") else f"/c/{node_id}"
    return RedirectResponse(dest, status_code=303)


@app.get("/l/{token}")
def login_link(token: str, next: str = ""):
    """A login link the bots send. Chat apps fetch every link they show, to build a preview, so a GET must not
    spend the token: it shows a Continue button, and the POST below signs the person in. `next` is an optional
    path on this site to land on instead of /me."""
    try:
        t = core.read_token(con, token, "login")
    except core.PBError:
        return page("Link expired", "<p>Ask the bot for a fresh link with /me.</p>")
    m = db.row(con.execute("SELECT display FROM members WHERE id=?", (t["member_id"],)).fetchone())
    dest = _back(next, "/me")
    return page("Continue", f"<p>Sign in as <b>{esc((m or {}).get('display') or 'yourself')}</b> on this phone.</p>"
                            f"<form method=post action='/l/{esc(token)}'><input type=hidden name=next value='{esc(dest)}'><button>Continue</button></form>")


@app.post("/l/{token}")
def login_link_go(token: str, next: str = Form("")):
    try:
        t = core.read_token(con, token, "login", consume=True)
    except core.PBError:
        return page("Link expired", "<p>Ask the bot for a fresh link with /me.</p>")
    return _set_session(RedirectResponse(_back(next, "/me"), status_code=303), t["member_id"])


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
    live, old_ = [], []
    for x in ms:
        rnd = core.current_round(con, x["node_id"])
        (live if rnd and rnd["status"] in LIVE_STAGES else old_).append((x, rnd))
    for x, rnd in live:
        body += f"<div class=card><b>{esc(x['name'])}</b> · {esc(x['status'])} · round: {esc(rnd['status'])}<br>"
        if x["status"] == "active":
            body += f"<a href='/n/{esc(x['node_id'])}'>Open</a>"
        if x["role"] in ("coordinator", "treasurer"):
            body += f" · <a href='/c/{esc(x['node_id'])}'>Coordinate</a>"
        body += "</div>"
    if not live:
        body += "<p>No round is open where you take part.</p>"
    if old_:
        body += "<p class=muted>Earlier: " + " · ".join(f"<a href='/c/{esc(x['node_id'])}'>{esc(x['name'])}</a>" if x["role"] in ("coordinator", "treasurer") else esc(x["name"]) for x, _ in old_) + "</p>"
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
        <input name=upi placeholder='The vendor&#39;s UPI id, if you have a quote (optional)'>
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
def propose(node_id: str, request: Request, title: str = Form(...), text: str = Form(""), ideal: int = Form(...), min: int = Form(0), upi: str = Form(""), lead: str | None = Form(None), next: str = ""):
    m = _require(request); rnd = core.current_round(con, node_id)
    if not rnd: raise HTTPException(400, "no open round")
    try:
        core.add_proposal(con, rnd["id"], m["id"], title=title, text=text or title, ideal_paise=ideal * 100, min_paise=min * 100, lead_id=m["id"] if lead else None, payee_upi=upi)
    except core.PBError as e:
        raise HTTPException(400, e.code)
    return RedirectResponse(_back(next, f"/n/{node_id}"), status_code=303)


@app.post("/n/{node_id:path}/lead")
def lead(node_id: str, request: Request, pid: str = Form(...), next: str = ""):
    m = _require(request)
    try: p = core.lead_proposal(con, pid, m["id"])
    except core.PBError as e:
        if request.headers.get("x-requested-with") == "fetch": return JSONResponse({"error": e.code}, status_code=400)
        raise HTTPException(400, e.code)
    if request.headers.get("x-requested-with") == "fetch": return JSONResponse(_prop_state(node_id, p))
    return RedirectResponse(_back(next, f"/n/{node_id}"), status_code=303)


def _prop_state(pool_id: str, p: dict) -> dict:
    names = {k["id"]: k["name"] for k in core.children(con, pool_id)}
    signs = db.j(p["cosign_nodes"], [])
    return {"id": p["id"], "status": p["status"], "lead": bool(p["lead_id"]), "backers": [names.get(x, x) for x in signs], "signs": signs}


@app.post("/n/{node_id:path}/cosign")
def cosign(node_id: str, request: Request, pid: str = Form(...), next: str = ""):
    m = _require(request)
    via = core.require_in_tree(con, node_id, m["id"])
    try: p = core.cosign_proposal(con, pid, via["id"], m["id"])
    except core.PBError as e:
        if request.headers.get("x-requested-with") == "fetch": return JSONResponse({"error": e.code}, status_code=400)
        raise HTTPException(400, e.code)
    if request.headers.get("x-requested-with") == "fetch": return JSONResponse(_prop_state(node_id, p))
    return RedirectResponse(_back(next, f"/n/{node_id}"), status_code=303)


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
@app.get("/c/{node_id:path}/applications.csv")
def applications_csv(node_id: str, request: Request):
    """Everything the form collected, for the treasurer only. Never public: it carries phone numbers."""
    m = _require(request)
    if not core.is_role(con, node_id, m["id"], ("coordinator", "treasurer")): raise HTTPException(403)
    import csv
    import io
    from fastapi.responses import Response
    buf = io.StringIO(); w = csv.writer(buf)
    cols = ["business", "applicant", "role", "phone", "about", "place_id", "pin", "address", "challenge", "team_size", "need", "share", "contribute", "consent", "status", "node_id", "created_at"]
    w.writerow(cols)
    for a in core.applications_of(con, node_id):
        w.writerow([a.get(c, "") for c in cols])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={node_id.replace('/', '-')}-registrations.csv"})


def _runs(node_id: str, member_id: str) -> bool:
    """Coordinators and treasurers of the node, and the treasurer of its pool."""
    if core.is_role(con, node_id, member_id, ("coordinator", "treasurer")):
        return True
    node = core.get_node(con, node_id)
    return bool(node.get("parent_id")) and core.is_role(con, node["parent_id"], member_id, ("treasurer",))


@app.get("/c/{node_id:path}")
def coordinate(node_id: str, request: Request):
    m = _require(request)
    if not _runs(node_id, m["id"]): raise HTTPException(403)
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
    node = core.get_node(con, node_id)
    base = str(request.base_url).rstrip("/")
    if node.get("parent_id") and not node.get("charter_at") and core.is_role(con, node_id, m["id"], ("coordinator",)):
        from . import convo
        par = core.get_node(con, node["parent_id"])
        body += (f"<div class=card><h2 style='margin-top:0'>{esc(par['name'])} works like this</h2><ol class=rules>" + "".join(f"<li>{esc(ln)}</li>" for ln in convo.CHARTER_LINES) + "</ol>"
                 f"<form method=post action='/n/{esc(node_id)}/charter'><button>Agree and continue</button></form></div>")
    if not node.get("parent_id"):
        rnd_p = core.current_round(con, node_id)
        open_needs = core.needs_of(con, node_id)
        drafts_n = db.row(con.execute("SELECT COUNT(*) AS c FROM proposals WHERE round_id=? AND source='twin'", (rnd_p["id"],)).fetchone())["c"] if rnd_p else 0
        body += (f"<h2>The twin</h2><p class=muted>{len(open_needs)} open need{'s' if len(open_needs) != 1 else ''} from {len({n['node_id'] for n in open_needs})} businesses · {drafts_n} draft{'s' if drafts_n != 1 else ''} in this round. "
                 "It runs on its own after each business joins; this runs it now.</p>"
                 f"<form method=post action='/c/{esc(node_id)}/twin'><button class=small>Draft proposals from needs</button></form>")
        apps = core.applications_of(con, node_id)
        body += f"<h2>Registered businesses</h2><p class=muted>From <a href='/apply/{esc(node_id)}'>{esc(base)}/apply/{esc(node_id)}</a>. "
        body += f"A business registers and runs itself at once; “Verify” is your note that you've checked it is real. “Send card” is for owners who registered but never continued. <a href='/c/{esc(node_id)}/applications.csv'>Download CSV</a>.</p>"
        if apps:
            body += "<table><tr><th>Business</th><th>Who</th><th>Team</th><th>Need</th><th></th></tr>"
            for a in apps:
                link = f"{base}/j/{a['token']}"
                if os.environ.get("PB_WA_NUMBER"):
                    from . import whatsapp
                    link = whatsapp.join_link(a["token"])
                msg = f"Hi {a['applicant']}, welcome to {node['name']}. Open this to join as {a['business']}: {link}"
                call = f"<a href='https://wa.me/{esc(a['phone'])}'>WhatsApp</a>"
                if a["status"] == "verified":
                    act = f"<span class=ok>verified</span> · {call}"
                elif a["status"] == "joined":
                    act = (f"<form method=post action='/c/{esc(node_id)}/verify' style='margin:0'><input type=hidden name=application_id value='{esc(a['id'])}'>"
                           f"<button class=small>Verify</button></form>{call}")
                else:
                    act = f"<a href='https://wa.me/{esc(a['phone'])}?text={esc(urllib.parse.quote(msg))}'>Send card</a>"
                chal = f"<br><span class=muted>Needs help with: {esc(a['challenge'])}</span>" if a.get("challenge") else ""
                where = " · ".join(x for x in ((places.get(a["place_id"]) or {}).get("name", "") if a.get("place_id") else "", a.get("pin") or "", a.get("address") or "") if x)
                body += (f"<tr><td><b>{esc(a['business'])}</b><br><span class=muted>{esc(a['about'] or '')}</span>" + (f"<br><span class=muted>{esc(where)}</span>" if where else "") + f"{chal}</td><td>{esc(a['applicant'])}<br><span class=muted>{esc(a['role'])}"
                         + (" · would chip in" if a["contribute"] == "yes" else "") + f"</span></td><td>{a['team_size'] or ''}</td><td>{esc(a['need'] or '')}</td><td>{act}</td></tr>")
            body += "</table>"
        else:
            body += "<p class=muted>Nobody yet.</p>"
    tok = core.mint_token(con, "join", node_id=node_id, ttl=core.TOKEN_TTL_JOIN)
    body += f"<h2>Join link (put this on the poster)</h2><p><code>{esc(base)}/j/{esc(tok)}</code></p>"
    if BOT_USERNAME: body += f"<p><code>https://t.me/{BOT_USERNAME}?start=j_{esc(tok)}</code></p>"
    if os.environ.get("PB_WA_NUMBER"):
        from . import whatsapp
        body += f"<p>WhatsApp: <code>{esc(whatsapp.join_link(tok))}</code> · <a href='/j/{esc(tok)}/poster.png?via=wa'>card</a></p>"
    return page(f"Coordinate · {node['name']}", body)


@app.post("/c/{node_id:path}/twin")
def run_twin(node_id: str, request: Request):
    m = _require(request)
    if not core.is_role(con, node_id, m["id"], ("coordinator", "treasurer")): raise HTTPException(403)
    from . import twin
    try:
        made = twin.draft_from_needs(con, node_id)
    except core.PBError as e:
        raise HTTPException(400, e.code)
    return RedirectResponse(f"/c/{node_id}?drafted={len(made)}", status_code=303)


@app.post("/c/{node_id:path}/verify")
def verify_application(node_id: str, request: Request, application_id: str = Form(...)):
    m = _require(request)
    try: core.verify_application(con, application_id, m["id"])
    except core.PBError as e: raise HTTPException(403, e.code)
    return RedirectResponse(f"/c/{node_id}", status_code=303)


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
    "winner": ("A winning plan was picked for {node}. Please approve or reject it; nothing is paid until the plan is approved. {cmd}\n{url}", "/approve", "APPROVE"),
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


def _notify_treasurers(pool_id: str, text: str) -> None:
    """A Telegram message to each treasurer of the pool who has linked Telegram. Sent inside the request (Cloud Run
    gives threads no CPU afterwards) and never allowed to fail the request."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        return
    for mem in db.rows(con.execute("SELECT m.telegram_id FROM members m JOIN memberships ms ON ms.member_id=m.id WHERE ms.node_id=? AND ms.role='treasurer' AND ms.status='active' AND m.telegram_id IS NOT NULL", (pool_id,)).fetchall()):
        try:
            httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": mem["telegram_id"], "text": text}, timeout=8)
        except Exception:
            pass


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
    body += "</table><p class=muted>Each payout goes to the vendor named on the proposal, or to the lead if none was named. Put the round id in the UPI remark.</p>"
    return page("Treasury · payout queue", body)


@app.get("/pay/{payout_id}")
def pay(payout_id: str, request: Request):
    """The treasurer's hand-off to their UPI app: a page with the upi:// link, which also tries to open it at once.
    Chat apps refuse upi:// on buttons, so the bots link here instead."""
    m = _require(request)
    if not db.row(con.execute("SELECT 1 FROM memberships WHERE member_id=? AND role='treasurer' AND status='active'", (m["id"],)).fetchone()): raise HTTPException(403)
    x = next((q for q in core.payout_queue(con) if q["id"] == payout_id), None)
    if not x: raise HTTPException(404)
    pa = x["payee_upi"] or x["lead_upi"]
    if not pa:
        return page("No UPI id yet", f"<p>{esc(x['title'])} · {_rupees(x['amount_paise'])}. The payee hasn't given a UPI id. <a href='/treasury'>Treasury</a></p>")
    note = f"{x['round_id']} {x['title']}"
    link = core.upi_link(pa, x["amount_paise"], note)
    body = (f"<p><b>{esc(x['title'])}</b> · {_rupees(x['amount_paise'])} · {esc(x['kind'])}</p><p>to <code>{esc(pa)}</code></p>"
            f"<p><a href='{esc(link)}'><button>Open UPI app</button></a></p>"
            f"<p class=muted>If nothing opens, copy the id above into your UPI app and pay {_rupees(x['amount_paise'])}. Then tap \"Mark paid\" in the bot or on the <a href='/treasury'>treasury page</a> and enter the reference.</p>"
            f"<script>setTimeout(function(){{location.href={json.dumps(link)}}},400)</script>")
    return page("Pay", body)


@app.post("/treasury/paid")
def paid(request: Request, payout_id: str = Form(...), reference: str = Form(...)):
    m = _require(request)
    if not db.row(con.execute("SELECT 1 FROM memberships WHERE member_id=? AND role='treasurer' AND status='active'", (m["id"],)).fetchone()): raise HTTPException(403)
    core.mark_paid(con, payout_id, reference)
    return RedirectResponse("/treasury", status_code=303)


# ----------------------------------------------------------------------------
# the live board: one public, read-only page per node, for a projector or a phone
# ----------------------------------------------------------------------------
BOARD_CSS = """<style>body{max-width:1040px;padding-top:24px}
.bar{display:flex;align-items:center;gap:10px;background:var(--ink);color:var(--paper);border:2px solid var(--ink);border-radius:3px 3px 0 0;padding:9px 14px;font:400 10px/1 Silkscreen,"IBM Plex Mono",monospace;text-transform:uppercase}
.bar .sq{width:10px;height:10px;border:2px solid var(--paper)}.bar a{color:var(--paper);text-decoration:none}
.win{border:2px solid var(--ink);border-top:0;background:var(--card);padding:18px 22px 30px;box-shadow:6px 6px 0 #20201dcc;border-radius:0 0 3px 3px}
.win h1{margin-top:4px}.win .card{box-shadow:none;border-width:1px;border-color:var(--line);background:var(--paper)}
.stage{display:inline-block;padding:7px 14px;border-radius:10px;background:var(--gold);color:var(--ink);font:400 11px/1 Silkscreen,"IBM Plex Mono",monospace;text-transform:uppercase;vertical-align:middle;border:2px solid var(--ink)}
.big{font:400 44px/1.1 Newsreader,Georgia,serif;color:var(--ink);vertical-align:middle}
table{font:400 15px/1.4 "IBM Plex Mono",monospace}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:700px){.grid{grid-template-columns:1fr}body{padding:16px 12px}.win{padding:12px 12px 20px}.big{font-size:34px}}</style>"""
STAGE_WORDS = {"proposals": "Proposals open", "ballots": "Voting", "plans": "Plans ready, ranking", "ratifying": "Approval",
               "ratified": "Approved", "failed": "Not approved", "paid": "Paid out", "closed": "Closed"}


@app.get("/b/{node_id:path}")
def board(node_id: str):
    """Everything the room may see. No names, no ids, no UPI handles; the payer never appears."""
    node_id = _resolve(node_id)
    try:
        node = core.get_node(con, node_id)
    except core.PBError:
        raise HTTPException(404)
    body = _board_body(node)
    html = page(node["name"], body).body.decode()
    bar = f"<div class=bar><span class=sq></span><a href='/'>{esc(BRAND)}</a> &nbsp;·&nbsp; {esc(node['id'].rsplit('/', 1)[-1])}.log</div><div class=win>"
    html = html.replace(f"<p class=brand><a href='/'>{esc(BRAND)}</a></p>", bar, 1) + "</div>"
    return HTMLResponse(html.replace(CSS, CSS + BOARD_CSS, 1).replace("<title>", "<meta http-equiv=refresh content=15><title>", 1))   # board styles after the page's, so they win


def _board_body(node: dict, with_pot: bool = True) -> str:
    """The public record of a node's round as HTML: businesses, needs, proposals, plans, approval, ledger."""
    node_id = node["id"]
    kids = core.children(con, node_id)
    rnd = core.current_round(con, node_id)
    names = {k["id"]: k["name"] for k in kids}
    body = ""
    if node.get("parent_id"):
        par = core.get_node(con, node["parent_id"])
        body += f"<p class=muted>Part of <a href='/b/{esc(par['id'])}'>{esc(par['name'])}</a></p>"
    if not rnd:
        body += "<p class=big>No round open</p>"
    elif not with_pot:
        pass
    else:
        st = rnd["status"]
        spend = core.spendable_budget(con, rnd)
        inn = core.contributions_in(con, rnd["id"])
        n_in = len({c['from_node_id'] for c in db.rows(con.execute('SELECT from_node_id FROM contributions WHERE to_round_id=?', (rnd['id'],)).fetchall())})
        if node.get("match_pct") and not inn:
            # a matching pool before anyone has put money in: show the ceiling and the rule, not a zero
            times = f"{node['match_pct'] / 100:g}×"
            body += (f"<p><span class=stage>{esc(STAGE_WORDS.get(st, st))}</span> &nbsp; <span class=big>up to {_rupees(rnd['budget_paise'])}</span> "
                     f"<span class=muted>· the pot matches what businesses put in, {times}. Nothing in yet.</span></p>")
        else:
            body += f"<p><span class=stage>{esc(STAGE_WORDS.get(st, st))}</span> &nbsp; <span class=big>{_rupees(spend)}</span>"
            if kids and node.get("match_pct"):
                body += f" <span class=muted>= {_rupees(inn)} from {n_in} nodes + match {node['match_pct'] / 100:g}× (up to {_rupees(rnd['budget_paise'])})</span>"
            elif kids and inn:
                body += f" <span class=muted>= the pot {_rupees(rnd['budget_paise'])} + {_rupees(inn)} put in by {n_in} businesses</span>"
            body += "</p>"
    if kids:
        contributed = {}
        if rnd:
            for c in db.rows(con.execute("SELECT from_node_id, SUM(amount_paise) AS a FROM contributions WHERE to_round_id=? GROUP BY from_node_id", (rnd["id"],)).fetchall()):
                contributed[c["from_node_id"]] = c["a"]
        what = "business" if node.get("pool_vote") == "nodes" else "node"
        body += f"<h2>{len(kids)} {what}{'es' if what == 'business' else 's'} · {'one vote each' if node.get('pool_vote') == 'nodes' else 'every member votes'}</h2><table><tr><th>{what.capitalize()}</th><th>Members</th><th>In the pool</th><th>Joined</th><th>Contributed</th></tr>"
        for k in sorted(kids, key=lambda k: k["created_at"]):
            body += f"<tr><td>{esc(k['name'])}</td><td>{len(core.active_members(con, k['id']))}</td><td>{'yes' if k.get('charter_at') else '—'}</td><td class=muted>{esc(core._ist(k['created_at']) or '')}</td><td>{_rupees(contributed[k['id']]) if k['id'] in contributed else '—'}</td></tr>"
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
                sch = db.j(p.get("scheme_json"), None) if p.get("scheme_json") else None
                if sch: body += f"<br><span class=muted>a scheme could carry part of it: {esc(sch['name'])} ({esc(sch['share'])})</span>"
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
    return body


# ----------------------------------------------------------------------------
# the front door: a pool's registration form, and the landing page
# ----------------------------------------------------------------------------
APPLY_INTRO = os.environ.get("PB_APPLY_INTRO", (
    "A shared ₹5L fund for things businesses can buy or run together: a delivery scooter, a shared software licence, "
    "an AI training. Each business says what it needs, similar needs are grouped, then we all vote on what we fund."))


HELP_OPTIONS = [("equipment", "Equipment"), ("staff training", "Staff training"), ("marketing", "Marketing"), ("ai tools", "AI tools"),
                ("transport or delivery", "Transport / delivery"), ("finance", "Finance")]
SHARE_OPTIONS = [("equipment", "Equipment"), ("space", "Space"), ("staff time", "Staff time"), ("a skill or training", "A skill or training"),
                 ("co-marketing", "Co-marketing"), ("contacts", "Contacts")]


def _joined(picked: list[str], other: str, options) -> str:
    allowed = {val for val, _ in options}
    out = [x for x in picked if x in allowed]
    if other.strip():
        out.append(other.strip()[:120])
    return ", ".join(out)


def _apply_form(pool: dict, v: dict | None = None, error: str = "", path: str | None = None) -> HTMLResponse:
    v = v or {}
    path = path or pool["id"]
    g = lambda k: esc(v.get(k, ""))  # noqa: E731
    def radio(name, options, default=""):
        cur = v.get(name, default)
        return "<div class=choice>" + "".join(f"<label><input type=radio name={name} value={val}{' checked' if cur == val else ''}{' required' if i == 0 else ''}>{lab}</label>" for i, (val, lab) in enumerate(options)) + "</div>"
    def checks(name, options):
        cur = set(v.get(name, []) if isinstance(v.get(name), list) else [x.strip() for x in str(v.get(name, "")).split(",") if x.strip()])
        tiles = "".join(f"<label><input type=checkbox name={name} value='{esc(val)}'{' checked' if val in cur else ''}>{lab}</label>" for val, lab in options)
        other = esc(v.get(name + "_other", ""))
        return f"<div class='choice multi'>{tiles}</div><input name={name}_other placeholder='Something else?' maxlength=120 value='{other}'>"
    body = f"<p>{esc(APPLY_INTRO)}</p>"
    if error:
        body += f"<p class=bad>{esc(error)}</p>"
    body += f"""<form method=post action='/apply/{esc(path)}'>
<input class=hp name=website tabindex=-1 autocomplete=off>
<label>Business name <input name=business required maxlength=80 value='{g('business')}'></label>
<label>Your name <input name=applicant required maxlength=80 value='{g('applicant')}'></label>
<label>Your role {radio('role', [('owner', 'Owner'), ('manager', 'Manager'), ('staff', 'Staff')])}</label>
<label>What the business does <span class=hint>e.g. tailoring, or a cafe</span>
<input name=about required maxlength=200 value='{g('about')}'></label>
<label>Where is the shop? Village panchayat or town <span class=hint>Start typing and pick from the list, or tap the button and we fill it in from where you are standing. The taluka and district follow from it.</span>
<div class=row style='gap:8px;align-items:stretch'><input list=pb-places name=place maxlength=80 autocomplete=off value='{g('place')}' style='flex:1;width:auto'><button type=button class='small quiet' id=pb-locate style='width:auto;margin:8px 0 0;white-space:nowrap'>Use my location</button></div>
<input type=hidden name=place_id value='{g('place_id')}'><span class=hint id=pb-place-hint></span></label>
<label>Pin code <span class=hint>Optional.</span><input name=pin inputmode=numeric pattern='[0-9]{{6}}' maxlength=6 placeholder='403527' value='{g('pin')}' style='width:12ch'></label>
<label>House number, lane or landmark <span class=hint>Optional. So we can find the shop; it is never shown publicly.</span>
<input name=address maxlength=200 autocomplete=street-address value='{g('address')}'></label>
<datalist id=pb-places>{_place_options()}</datalist>
<script>(function(){{var P={_places_json()};var place=document.querySelector('input[name=place]'),pid=document.querySelector('input[name=place_id]'),addr=document.querySelector('input[name=address]'),hint=document.getElementById('pb-place-hint'),btn=document.getElementById('pb-locate');
function norm(s){{return (s||'').toLowerCase().replace(/[^a-z]/g,'')}}
function set(p,how){{place.value=p.label;pid.value=p.id;hint.textContent='✓ '+p.label+(how?' · '+how:'');hint.className='hint ok'}}
function match(){{var n=norm(place.value);if(!n){{pid.value='';hint.textContent='';return}}var hit=P.filter(function(p){{return norm(p.label)===n||norm(p.label.split('·')[0])===n}})[0];if(hit){{set(hit,'')}}else{{pid.value='';hint.textContent='Pick a name from the list, or leave it and we will work it out from the address.';hint.className='hint'}}}}
place.addEventListener('change',match);place.addEventListener('input',function(){{if(P.some(function(p){{return p.label===place.value}}))match()}});
var t;addr.addEventListener('input',function(){{clearTimeout(t);t=setTimeout(function(){{if(place.value)return;var words=norm(addr.value);if(words.length<4)return;var best=null;P.forEach(function(p){{var k=norm(p.label.split('·')[0]);if(k.length>3&&words.indexOf(k)>=0&&(!best||k.length>best.k.length))best={{p:p,k:k}}}});
  if(best){{hint.innerHTML='';var b=document.createElement('button');b.type='button';b.className='small quiet';b.style.cssText='width:auto;margin:6px 0 0';b.textContent='Looks like '+best.p.label+' — use it';b.addEventListener('click',function(){{set(best.p,'from the address')}});hint.appendChild(b)}}}},400)}});
btn.addEventListener('click',function(){{if(!navigator.geolocation){{hint.textContent='This phone will not share its location.';return}}hint.textContent='Finding where you are…';hint.className='hint';
  navigator.geolocation.getCurrentPosition(function(pos){{fetch('/twin/locate?lat='+pos.coords.latitude.toFixed(5)+'&lon='+pos.coords.longitude.toFixed(5)).then(function(r){{return r.ok?r.json():null}}).then(function(p){{if(!p){{hint.textContent='That spot is not inside any Goan local body we know; type the name instead.';return}}set(p,'from your location');if(!addr.value)addr.value=p.name}}).catch(function(){{hint.textContent='Could not look that up; type the name instead.'}})}},function(){{hint.textContent='Location was not shared; type the name instead.'}},{{timeout:8000,maximumAge:60000}})}});
if(place.value)match()}})()</script>
<label>What do you need help with most? <span class=hint>Tick all that apply.</span>{checks('challenge', HELP_OPTIONS)}</label>
<label>How many people work there, including you <input name=team_size type=number inputmode=numeric min=1 max=500 required value='{g('team_size') or 1}'></label>
<label>What would you buy or run together with other businesses? <span class=hint>One line. A delivery scooter for the lane, one billing licence for several shops, a cold store, a trainer, anything.</span>
<textarea name=need rows=2 required maxlength=500>{g('need')}</textarea></label>
<label>What could you share with other businesses? <span class=hint>Tick all that apply. Nothing is too small.</span>{checks('share', SHARE_OPTIONS)}</label>
<label>Does the business hold any of these? <span class=hint>Optional. They are the entry tickets to most government schemes; we'll show you which ones open up.</span>
<div class='choice multi'>{''.join(f"<label><input type=checkbox name=registrations value={val}{' checked' if val in (v.get('registrations') or []) else ''}>{lab}</label>" for val, lab in REG_OPTIONS)}</div></label>
<label>How old is the business? <span class=hint>Optional.</span>{radio('age_band', AGE_OPTIONS)}</label>
<label>WhatsApp number <span class=hint>We may call you to verify your eligibility.</span>
<div class=phone><span class=cc><select name=cc aria-label=Country>{_country_options(v.get('cc', 'IN'))}</select><span class=ccv aria-hidden=true></span></span><input name=phone type=tel inputmode=tel required placeholder='98765 43210' value='{g('phone')}'></div></label>
<script>(function(){{var s=document.querySelector('select[name=cc]'),v=document.querySelector('.ccv');if(!s||!v)return;function show(){{v.textContent=s.options[s.selectedIndex].dataset.short}}show();s.addEventListener('change',show)}})()</script>
<button>Register my business</button>
</form>
<p class=muted>Free and voluntary. Registering does not guarantee funding. By registering you agree to our <a href='/privacy'>privacy policy</a>.</p>"""
    return page(pool["name"], body)


REG_OPTIONS = [("udyam", "Udyam / MSME number"), ("startup", "Startup Goa certificate"), ("gst", "GST number"), ("none", "None of these")]
AGE_OPTIONS = [("new", "Under a year"), ("young", "1 to 5 years"), ("established", "Older")]


def _place_options() -> str:
    from . import places
    return "".join(f"<option value='{esc(c['label'])}'>" for c in places.choices())


def _places_json() -> str:
    from . import places
    return json.dumps([{"label": c["label"], "id": c["id"]} for c in places.choices()], ensure_ascii=False).replace("</", "<\\/")


def _country_options(selected: str) -> str:
    from .countries import COUNTRIES, flag
    # every option carries the full name; the closed box shows only flag and prefix through the .ccv overlay
    return "".join(f"<option value={iso} data-short='{flag(iso)} +{code}'{' selected' if iso == selected else ''}>{flag(iso)} +{code} {esc(name)}</option>" for iso, name, code in COUNTRIES)


# Short public names for node ids: /apply/goa and /b/goa instead of the full id. "goa=goa-business-commons,x=y".
ALIASES = dict(kv.split("=", 1) for kv in os.environ.get("PB_ALIASES", "goa=goa-business-commons").split(",") if "=" in kv)


def _resolve(node_id: str) -> str:
    return ALIASES.get(node_id, node_id)


def _pool_or_404(pool_id: str) -> dict:
    try:
        pool = core.get_node(con, _resolve(pool_id))
    except core.PBError:
        raise HTTPException(404)
    if pool.get("parent_id"):
        raise HTTPException(404)
    return pool


@app.get("/apply/{pool_id:path}")
def apply_get(pool_id: str):
    return _apply_form(_pool_or_404(pool_id), path=pool_id)


@app.post("/apply/{pool_id:path}")
def apply_post(pool_id: str, request: Request, business: str = Form(""), applicant: str = Form(""), role: str = Form(""), phone: str = Form(""), cc: str = Form("IN"),
               about: str = Form(""), challenge: list[str] = Form([]), challenge_other: str = Form(""), team_size: str = Form(""), need: str = Form(""),
               share: list[str] = Form([]), share_other: str = Form(""), contribute: str = Form(""), consent: str = Form(""), website: str = Form(""),
               address: str = Form(""), place: str = Form(""), place_id: str = Form(""), pin: str = Form(""), registrations: list[str] = Form([]), age_band: str = Form("")):
    pool = _pool_or_404(pool_id)
    from .countries import DIAL
    from . import places
    cc = cc.upper() if cc.upper() in DIAL else "IN"
    v = dict(business=business, applicant=applicant, role=role, phone=phone, cc=cc, about=about, challenge=challenge, challenge_other=challenge_other, team_size=team_size,
             need=need, share=share, share_other=share_other, contribute=contribute, consent=consent, address=address, place=place, place_id=place_id,
             registrations=registrations, age_band=age_band, pin=pin)
    # the place: what the picker set, else the typed name, else a name found in the address
    pl = places.get(place_id) if place_id else None
    if not pl and place:
        pl = places.resolve(place)
    if not pl and address:
        pl = next((places.resolve(w) for w in sorted(re.findall(r"[A-Za-z][A-Za-z.\-]{3,}", address), key=len, reverse=True) if places.resolve(w)), None)
    place_id = pl["id"] if pl and pl["kind"] not in places.ROLLUPS else ""
    if website:   # a bot filled the hidden field: say thanks, keep nothing
        return page("Thank you", "<p>We'll be in touch.</p>")
    challenge = _joined(challenge, challenge_other, HELP_OPTIONS); share = _joined(share, share_other, SHARE_OPTIONS)
    if not challenge or not share:
        return _apply_form(pool, v, "Tick at least one box under 'help with' and under 'share'.", path=pool_id)
    if not core.normalize_phone(phone, DIAL[cc]):
        return _apply_form(pool, v, "That doesn't look like a WhatsApp number. The number without the country code; the code is in the box next to it.", path=pool_id)
    try:
        a = core.apply(con, pool["id"], business=business, applicant=applicant, role=role, phone=phone, dial=DIAL[cc], about=about, challenge=challenge,
                       team_size=int(team_size or 1) if (team_size or "1").isdigit() else 1, need=need, share=share, contribute=contribute, consent=bool(consent),
                       address=address, place_id=place_id or None, registrations=",".join(registrations), age_band=age_band, pin=pin)
    except core.PBError:
        return _apply_form(pool, v, "Check the business name and your name.", path=pool_id)
    if a["status"] == "new":
        m = core.enter_from_application(con, a["id"])
        _notify_treasurers(pool["id"], (f"New business on {pool['name']}: {a['business']} — {a['applicant']} ({a['role']}), team of {a['team_size']}, {a['about']}"
                                        + (f", at {a['address']}" if a.get("address") else "") + (f" ({pl['name']})" if pl else "") + ".\n"
                                        f"Needs help with: {a['challenge']}.\nWould buy or run together: {a['need']}\n"
                                        f"Verify: {BASE_URL}/c/{pool['id']}"))
        return _set_session(RedirectResponse(f"/start/{a['node_id']}/values", status_code=303), m["id"])
    # the same number again, after already joining: back into that business's flow if this browser runs it
    m = _member(request)
    if m and core.is_role(con, a["node_id"], m["id"], ("coordinator",)):
        return page("Already registered", f"<p><b>{esc(a['business'])}</b> is already set up on this phone.</p>"
                                          f"<form method=get action='/start/{esc(a['node_id'])}/done'><button>Continue where you left off</button></form>")
    return page("Already registered", f"<p><b>{esc(a['business'])}</b> is already set up. Open the link we sent you, or ask in the group.</p>")


GROUP = os.environ.get("PB_WA_GROUP", "https://chat.whatsapp.com/Bmvu9ldoM8jIr1QZ3Qkmgu")   # the WhatsApp group where the Commons talks


@app.get("/privacy")
def privacy():
    body = f"""<p>This is what happens to what you type into the registration form.</p>
<h2>What we collect</h2>
<p>What you put in the registration form, including your WhatsApp number and the shop's address. If you tap “Use my location”, your phone's position is sent once to this site to find the village or town it falls in, and is not kept; nothing goes to any map company. Then, as you take part: what you propose, back, vote for and approve; what you write to the Commons number or to the group; and what you say to any assistant we run, human or AI.</p>
<h2>What it is for</h2>
<p>To run the Commons: to send you your business's join link, to group similar needs into proposals, to count one vote per business, and to call you if we need to confirm a business is real. We do not sell it or use it for advertising.</p>
<h2>WhatsApp</h2>
<p>The Commons runs on WhatsApp. Everyone in the <a href='{esc(GROUP)}'>group</a> can see everyone else's number, as in any WhatsApp group, and we may use your number to put you in touch with other businesses, for example the businesses backing the same proposal. Nothing more than that: we never hand numbers to anyone outside the Commons.</p>
<h2>What is public</h2>
<p>The live board and the export show business names, proposals, votes as totals, and the ledger of what was bought. Once we have verified a business, its name is also marked on the map in the village panchayat or town it sits in. They never show a person's name, a phone number, a street address, or a payment id.</p>
<h2>Research</h2>
<p>The Commons is an experiment in public goods, and we study it: the form answers, the votes and decisions, messages in the group and to the Commons number, and conversations with our assistants, including AI ones, may be read, analysed and summarised, by people and by software. We may publish what we learn about funding shared goods this way. In any research publication we remove names of people and businesses. In a small group someone who knows the businesses may still guess, so if there is something you would rather we did not use at all, tell a group admin and we won't. You may revoke your consent to be included in future research and publications at any time; what is already published stays published.</p>
<h2>How long we keep it, and how to leave</h2>
<p>We keep it while the Commons runs, and delete names and numbers when it ends. You can leave at any time: message an admin of the <a href='{esc(GROUP)}'>WhatsApp group</a> and we will delete what we hold about you and your business and stop using it. Decisions already made stay in the public record, without your name, because the other businesses' votes and the ledger depend on them.</p>
<p class=muted>Run by {esc(BRAND)}. This is a private, independent pilot, not a bank or a government scheme.</p>"""
    return page("Privacy", body)


def _budget(node: dict, rnd: dict | None) -> dict:
    """Where the pot stands: spendable, paid out, approved but unpaid, asked for by proposals on the table, remaining."""
    if not rnd:
        return {"pot": 0, "paid": 0, "approved": 0, "asked": 0, "remaining": 0, "proposals": 0, "stage": None}
    pot = core.spendable_budget(con, rnd)
    pay = db.rows(con.execute("SELECT status, amount_paise FROM payouts WHERE round_id=?", (rnd["id"],)).fetchall())
    paid = sum(x["amount_paise"] for x in pay if x["status"] in ("paid", "receipt_ok"))
    approved = sum(x["amount_paise"] for x in pay if x["status"] in ("queued", "sent"))
    props = db.rows(con.execute("SELECT ideal_paise FROM proposals WHERE round_id=? AND status IN ('draft','eligible')", (rnd["id"],)).fetchall())
    asked = sum(x["ideal_paise"] for x in props) if rnd["status"] == "proposals" else 0
    return {"pot": pot, "paid": paid, "approved": approved, "asked": asked, "remaining": max(0, pot - paid - approved), "proposals": len(props), "stage": rnd["status"]}


def _budget_bar(b: dict) -> str:
    pot = max(1, b["pot"])
    w = lambda v: f"{min(100, 100 * v / pot):.1f}%"  # noqa: E731
    asked_fit = min(b["asked"], b["remaining"])
    segs = (f"<span class='seg paid' style='width:{w(b['paid'])}' title='paid'></span><span class='seg approved' style='width:{w(b['approved'])}' title='approved, not yet paid'></span>"
            f"<span class='seg asked' style='width:{w(asked_fit)}' title='asked for by proposals on the table'></span>")
    legend = [f"<span><i class='sw paid'></i>paid {_rupees(b['paid'])}</span>", f"<span><i class='sw approved'></i>approved {_rupees(b['approved'])}</span>",
              f"<span><i class='sw asked'></i>asked for {_rupees(b['asked'])} in {b['proposals']} proposal{'s' if b['proposals'] != 1 else ''}</span>",
              f"<span><i class='sw left'></i>remaining {_rupees(b['remaining'])}</span>"]
    return f"<div class=budget><div class=track>{segs}</div><p class=legend>{' '.join(legend)}</p></div>"


def _steps(n: int) -> str:
    names = ["Register", "What matters", "You're in"]
    return "<ol class=steps>" + "".join(f"<li class='{'done' if i < n else 'now' if i == n else ''}'><span>{i + 1}</span>{x}</li>" for i, x in enumerate(names)) + "</ol>"


def _my_node_or_403(node_id: str, request: Request) -> tuple[dict, dict]:
    m = _require(request)
    if not core.is_role(con, node_id, m["id"], ("coordinator",)):
        raise HTTPException(403)
    return core.get_node(con, node_id), m


def _table_card(node: dict, parent: dict | None, m: dict, back: str, waiting: int = 0) -> str:
    """Proposals in the pool's open round, with lead and back buttons; a prominent propose button on top; and,
    while the business's own need has no draft yet, a drafting state that asks the server to draft (in a request,
    not a background thread: Cloud Run gives no CPU after a response) and swaps the card in without a reload."""
    pool = parent or node
    rnd = core.current_round(con, pool["id"])
    if not rnd or rnd["status"] != "proposals":
        return ""
    from .twin import rank_for
    props = rank_for(con, node["id"], db.rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND status IN ('draft','eligible') ORDER BY created_at DESC", (rnd["id"],)).fetchall()), limit=10)
    names = {k["id"]: k["name"] for k in core.children(con, pool["id"])}
    need = core.needs_of(con, node["id"], include_children=False, status=None)
    pending = bool(need) and any(n["status"] == "open" for n in need) and waiting < 3
    out = f"<div class='card rise' id=table data-draft='/start/{esc(node['id'])}/draft' data-back='{esc(back)}'><h2 style='margin-top:0'>On the table</h2>"
    out += ("<p class=muted>What businesses here could buy or run together. <b>Back</b> one if your business would use it. "
            "<b>Lead</b> one if you'd organise it and deal with the vendor; leading also backs it. Or add your own.</p>")
    out += (f"<details class=propose><summary>+ Propose your own</summary><form method=post action='/n/{esc(pool['id'])}/propose?next={esc(back)}'>"
            f"<label>What <input name=title required maxlength=120 placeholder='A shared delivery scooter for the market lane'></label>"
            f"<label>About how much, in rupees <input name=ideal type=number inputmode=numeric min=1 required placeholder='60000'></label>"
            f"<label>A line on how it would be shared <input name=text maxlength=500></label>"
            f"<label>The vendor's UPI id, if you have a quote <span class=hint>Optional. The fund pays them directly once the plan is approved.</span><input name=upi maxlength=80 placeholder='shop@okaxis'></label>"
            f"<input type=hidden name=lead value=on><button>Propose it, I'll lead it</button></form></details>")
    if pending:
        out += f"<p class=drafting id=drafting><span class=dot></span> Drafting a proposal from <i>{esc(need[-1]['text'])}</i>…</p>"
    if props:
        out += "<p class=muted>The ten that fit your business best, from what you told us.</p>"
        for p in props:
            signs = db.j(p["cosign_nodes"], [])
            out += f"<div class=prop data-pid='{esc(p['id'])}' data-me='{esc(node['id'])}'>" + ("<span class=tag>for you</span> " if p["suggested"] else "") + f"<b>{esc(p['title'])}</b> <span class=muted>· ideal {_rupees(p['ideal_paise'])}</span>"
            out += "<br><span class='muted backers'>" + ("backed by " + ", ".join(esc(names.get(x, x)) for x in signs) if signs else "") + "</span>"
            if p["source"] == "twin": out += "<br><span class=muted>suggested from what businesses said they need</span>"
            out += "<div class=row>"
            mine_lead = p["lead_id"] == m["id"]
            if mine_lead:
                out += "<span class=ok>you lead this ✓</span>"
            else:
                if not p["lead_id"]:
                    out += f"<form method=post action='/n/{esc(pool['id'])}/lead?next={esc(back)}'><input type=hidden name=pid value='{esc(p['id'])}'><button class=small>I'll lead this</button></form>"
                if node["id"] not in signs:
                    out += f"<form method=post action='/n/{esc(pool['id'])}/cosign?next={esc(back)}'><input type=hidden name=pid value='{esc(p['id'])}'><button class=small>Back this</button></form>"
                else:
                    out += "<span class=ok>backed ✓</span>"
            out += "</div></div>"
    elif not pending:
        out += "<p class=muted>Nothing on the table yet.</p>"
    out += "</div>"
    out += """<script>(function(){if(window.__pbTable)return;window.__pbTable=1;
document.addEventListener('submit',function(ev){var f=ev.target;if(!f.closest||!f.closest('.prop'))return;ev.preventDefault();var row=f.closest('.prop'),isLead=f.action.indexOf('/lead')>-1,rowDiv=row.querySelector('.row'),saved=rowDiv.innerHTML;
rowDiv.innerHTML='<span class=ok>'+(isLead?'you lead this ✓':'backed ✓')+'</span>';row.classList.add('did');
fetch(f.action,{method:'POST',body:new FormData(f),headers:{'X-Requested-With':'fetch'},credentials:'same-origin'}).then(function(r){return r.json().then(function(j){return [r.ok,j]})}).then(function(x){var ok=x[0],j=x[1];
if(!ok){rowDiv.innerHTML=saved;var e=row.querySelector('.err')||row.appendChild(Object.assign(document.createElement('span'),{className:'muted err'}));e.textContent=' '+(j.error||'that did not work').replace(/_/g,' ');return}
var bk=row.querySelector('.backers');bk.textContent=j.backers.length?'backed by '+j.backers.join(', '):'';
if(j.status==='eligible'&&!row.querySelector('.tag.vote')){row.insertBefore(Object.assign(document.createElement('span'),{className:'tag vote',textContent:'to the vote'}),row.firstChild)}}).catch(function(){rowDiv.innerHTML=saved})});
function draft(){var t=document.getElementById('table');if(!t||!document.getElementById('drafting'))return;
fetch(t.dataset.draft,{method:'POST',headers:{'X-Requested-With':'fetch'},credentials:'same-origin'}).then(function(r){return r.text()}).then(function(h){var d=document.createElement('div');d.innerHTML=h;var n=d.querySelector('#table');if(n){t.replaceWith(n);n.classList.add('did')}}).catch(function(){var p=document.getElementById('drafting');if(p){p.textContent='Could not draft just now. Your need is saved.'}})}
if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',draft)}else{draft()}})()</script>"""
    return out


@app.post("/start/{node_id:path}/draft")
def start_draft(node_id: str, request: Request):
    """The page asks for drafts from this business's need; the twin runs inside the request and the fresh card
    comes back as HTML."""
    node, m = _my_node_or_403(node_id, request)
    parent = core.get_node(con, node["parent_id"]) if node.get("parent_id") else None
    from . import twin
    try:
        twin.draft_from_needs(con, (parent or node)["id"])
    except core.PBError:
        pass
    return HTMLResponse(_table_card(node, parent, m, f"/start/{node['id']}/done", waiting=9))


@app.get("/start/{node_id:path}/values")
def start_values(node_id: str, request: Request):
    """Step 2: five sliders. The twin scores its drafts against these; asked again after a round."""
    node, m = _my_node_or_403(node_id, request)
    body = _steps(1) + f"<p><b>{esc(node['name'])}</b> is registered. One more minute. Five trade-offs: slide towards whichever matters more for your business this year. The middle means either is fine.</p>"
    body += f"<form method=post action='/start/{esc(node_id)}/values'>"
    for k, left, right, hint in core.VALUE_DIMENSIONS:
        body += (f"<div class='card rise axis'><div class=poles><span class=pole>{esc(left)}</span><span class=pole>{esc(right)}</span></div>"
                 f"<div class=slider><input id=v_{k} name={k} type=range min=0 max=10 value=5 aria-label='{esc(left)} or {esc(right)}'></div>"
                 f"<div class=poleword><output for=v_{k} data-l='{esc(left)}' data-r='{esc(right)}'>either</output></div><span class=hint>{esc(hint)}</span></div>")
    body += "<button>Next</button></form>"
    body += ("<script>document.querySelectorAll('.axis input').forEach(function(i){var o=i.closest('.axis').querySelector('output');function f(){var v=+i.value,c=v*10;"
             "i.style.setProperty('--a',Math.min(c,50)+'%');i.style.setProperty('--b',Math.max(c,50)+'%');"
             "o.textContent=v<5?'\u2190 '+o.dataset.l+(v<=1?', strongly':''):v>5?o.dataset.r+(v>=9?', strongly':'')+' \u2192':'either'}i.addEventListener('input',f);f()})</script>")
    return page("What matters to you", body)


@app.post("/start/{node_id:path}/values")
async def start_values_post(node_id: str, request: Request):
    node, m = _my_node_or_403(node_id, request)
    form = await request.form()
    core.set_values(con, node_id, m["id"], {k: form.get(k) for k, *_ in core.VALUE_DIMENSIONS})
    if node.get("parent_id"):
        with db.tx(con):   # registering and answering is joining: no separate rules step on the web
            con.execute("UPDATE nodes SET charter_at=COALESCE(charter_at,?) WHERE id=?", (db.now(), node_id))
    return RedirectResponse(f"/start/{node_id}/done", status_code=303)


@app.get("/start/{node_id:path}/done")
def start_done(node_id: str, request: Request):
    """Step 3: the charter to accept, the team link, what happens next."""
    node, m = _my_node_or_403(node_id, request)
    parent = core.get_node(con, node["parent_id"]) if node.get("parent_id") else None
    body = _steps(2) + f"<p class=rise><b>{esc(node['name'])}</b> is in {esc(parent['name']) if parent else esc(BRAND)}. You cast its vote.</p>"
    if True:
        waiting = int(request.query_params.get("w", "0") or 0)
        body += _table_card(node, parent, m, f"/start/{node['id']}/done", waiting=waiting)
        body += _business_twin_card(node)
        body += ("<div class='card rise'><h2 style='margin-top:0'>What happens next</h2><p>We'll reach out in the WhatsApp group when it's time to vote on final budgets.</p>"
                 "<p>Similar needs across businesses become draft proposals. You'll be asked to lead or back the ones that fit, "
                 "then every business gets 100 points to split across the list.</p></div>")
        body += f"<form method=get action='/start/{esc(node['id'])}/finished'><button>Finish</button></form>"
    return page("You're in", body)


def _seals(pool: dict, kids: list[dict]) -> str:
    """Who's in: one seal per business, a monogram in a ring, gold and ticked once the treasurer has verified it,
    with its place and the day it joined. Generated from the name, so every member has one from the moment it
    registers; a real logo can replace the monogram later."""
    import hashlib
    if not kids:
        return "<h2>Who's in</h2><p class=muted>Be the first.</p>"
    apps = {a["node_id"]: a for a in db.rows(con.execute("SELECT node_id, status, place_id FROM applications WHERE pool_id=?", (pool["id"],)).fetchall())}
    tiles = ""
    for k in sorted(kids, key=lambda k: k["created_at"]):
        a = apps.get(k["id"], {})
        verified = a.get("status") == "verified"
        words_ = [w for w in re.split(r"[\s&/-]+", k["name"]) if w and w[0].isalnum()]
        mono = "".join(w[0] for w in words_[:2]).upper() or k["name"][:1].upper()
        hue = int(hashlib.sha256(k["name"].encode()).hexdigest()[:4], 16) % 360
        place = (places.get(k.get("place_id") or a.get("place_id") or "") or {}).get("name") if (k.get("place_id") or a.get("place_id")) else None
        since = (core._ist(k["created_at"]) or "")[:10]
        tip = (f"{place} · " if place else "") + ("verified" if verified else f"since {since}")
        tiles += (f"<span class='seal{' v' if verified else ''}' title='{esc(tip)}'><svg viewBox='0 0 32 32' aria-hidden=true>"
                  f"<circle cx='16' cy='16' r='14' fill='hsl({hue} 38% 86%)'/><circle cx='16' cy='16' r='14' class='ring'/><text x='16' y='17'>{esc(mono)}</text></svg>"
                  f"{esc(k['name'])}{'<i aria-label=verified>✓</i>' if verified else ''}</span>")
    n_v = sum(1 for k in kids if apps.get(k["id"], {}).get("status") == "verified")
    return (f"<h2>Who's in <span class=sum>· {len(kids)} business{'es' if len(kids) != 1 else ''}{f', {n_v} verified' if n_v else ''}</span></h2>"
            f"<p class=seals>{tiles}</p>")


def _business_twin_card(node: dict) -> str:
    """On the owner's pages: what the government offers this business, one scheme per kind, and the step that opens the most."""
    from . import businesses
    c = businesses.card(con, node["id"])
    sm = c["summary"]
    out = (f"<div class='card rise'><h2 style='margin-top:0'>What the government offers {esc(node['name'])}</h2>"
           f"<p class=muted>Read from what you told the form, against {sm['schemes']} business schemes: <span class='tag ok'>eligible</span> {sm['eligible']} &nbsp; "
           f"<span class='tag maybe'>plausible</span> {sm['plausible']} &nbsp; <span class='tag no'>unlikely</span> {sm['unlikely']}.</p>")
    for t in c["slots"]:
        cls = "ok" if t["status"] == "eligible" else "maybe"
        link = f"<a href='{esc(t['source_url'])}'>{esc(t['name'])}</a>" if t["source_url"] else esc(t["name"])
        out += (f"<div class=prop><span class=slot style=\"display:block;font:400 10px/1 Silkscreen,'IBM Plex Mono',monospace;text-transform:uppercase;color:var(--muted);margin-bottom:5px\">{esc(t['slot_label'])}</span>"
                f"<span class='tag {cls}'>{t['status']}</span> <b>{link}</b> <span class=muted>· {esc(t['department'])}" + (f" · up to {_rupees(t['max_rupees'] * 100)}" if t["max_rupees"] else "")
                + (f" · <a href='{esc(t['apply_online'])}'>apply on Goa Online</a>" if t["apply_online"] else "") + f"</span><span class=why style='display:block;color:var(--muted);font-size:15px'>{esc(t['why'])}</span>"
                + "".join(f"<span class=step style='display:block;font-size:15px'>→ {esc(s)}</span>" for s in t["steps"]) + "</div>")
    if c["unlocks"]:
        u = c["unlocks"][0]
        out += f"<p><b>One step opens the most:</b> {esc(u['step'])} — {u['schemes']} more scheme{'s' if u['schemes'] != 1 else ''} become eligible.</p>"
    out += f"<a class=btn href='/schemes/{esc(node['id'])}'>All {sm['schemes']} business schemes for {esc(node['name'])}</a></div>"
    return out


@app.get("/schemes/{node_id:path}")
def business_schemes(node_id: str, request: Request):
    """The owner's full list: every business scheme with its verdict, grouped by status. Theirs alone."""
    node, m = _my_node_or_403(node_id, request)
    from . import businesses
    c = businesses.card(con, node_id)
    p = c["profile"]
    body = (f"<p class=muted>Read from what the form knows: trade {esc(', '.join(p['trades']) or 'not clear from the description')}; "
            + ("Udyam " + ("held" if p["udyam"] else "not held" if p["udyam"] is False else "unknown")) + "; "
            + ("Startup Goa " + ("held" if p["startup"] else "not held" if p["startup"] is False else "unknown")) + "; "
            + ("GST " + ("held" if p["gst"] else "not held" if p["gst"] is False else "unknown")) + f"; age {esc(p['age'] or 'unknown')}. "
            f"Change any of this by registering again from the same number.</p>")
    sm = c["summary"]
    body += (f"<p class=sum><span class='tag ok'>eligible</span> {sm['eligible']} &nbsp; <span class='tag maybe'>plausible</span> {sm['plausible']} &nbsp; "
             f"<span class='tag no'>unlikely</span> {sm['unlikely']} &nbsp;· of {sm['schemes']}</p>")
    if c["unlocks"]:
        body += "<div class=card><b>Steps that open the most</b>" + "".join(f"<p>→ {esc(u['step'])}: {u['schemes']} scheme{'s' if u['schemes'] != 1 else ''}</p>" for u in c["unlocks"]) + "</div>"
    order = {"eligible": 0, "plausible": 1, "unlikely": 2}
    body += "<div class=twin>"
    for status in ("eligible", "plausible", "unlikely"):
        rows_ = [r for r in c["all"] if r["status"] == status]
        if not rows_:
            continue
        cls = STATUS_WORDS[status][0]
        if status == "unlikely":
            body += f"<details class=fold><summary>{len(rows_)} unlikely for this business</summary>"
        else:
            body += f"<h2><span class='tag {cls}'>{status}</span> {len(rows_)}</h2>"
        for r in rows_:
            link = f"<a href='{esc(r['source_url'])}'>{esc(r['name'])}</a>" if r["source_url"] else esc(r["name"])
            body += (f"<div class=prop><b>{link}</b> <span class=muted>· {esc(r['department'])}" + (f" · up to {_rupees(r['max_rupees'] * 100)}" if r["max_rupees"] else "")
                     + (f" · <a href='{esc(r['apply_online'])}'>apply on Goa Online</a>" if r["apply_online"] else "") + f"</span><span class=why>{esc('; '.join(r['why']))}</span>"
                     + "".join(f"<span class=step>{esc(s)}</span>" for s in r["steps"]) + "</div>")
        if status == "unlikely":
            body += "</details>"
    body += "</div>"
    html = page(node["name"], body).body.decode()
    return HTMLResponse(html.replace(CSS, CSS + TWIN_CSS, 1))


@app.get("/start/{node_id:path}/finished")
def start_finished(node_id: str, request: Request):
    """The last screen: a small celebration, then the two things worth keeping."""
    node, m = _my_node_or_403(node_id, request)
    parent = core.get_node(con, node["parent_id"]) if node.get("parent_id") else None
    base = str(request.base_url).rstrip("/")
    tok = core.mint_token(con, "join_team", node_id=node["id"], ttl=core.TOKEN_TTL_JOIN)
    link = f"{base}/j/{tok}"
    import random
    rnd = random.Random(node_id)
    bits = "".join(f"<i style='left:{rnd.randint(2, 98)}%;animation-delay:{rnd.random() * .9:.2f}s;animation-duration:{1.6 + rnd.random() * 1.4:.2f}s;background:{rnd.choice(['#d9a33d', '#20201d', '#2f6b3f', '#e0b2a9', '#86bcc1'])};transform:rotate({rnd.randint(0, 360)}deg)'></i>" for _ in range(42))
    body = f"<div class=confetti aria-hidden=true>{bits}</div>" + _steps(3)
    body += ("<div class='tick big'><svg viewBox='0 0 52 52'><circle cx=26 cy=26 r=24 fill=none /><path fill=none d='M14 27l8 8 16-16'/></svg></div>"
             f"<p class='rise lead'>{esc(node['name'])} is in {esc(parent['name']) if parent else esc(BRAND)}. That's it.</p>")
    body += (f"<div class='card rise'><h2 style='margin-top:0'>One thing to do now</h2><p>Send your team the link so they can vote too.</p>"
             f"<a class=btn href='https://wa.me/?text={esc(urllib.parse.quote(f'Join {node['name']} on {parent['name'] if parent else BRAND}: {link}'))}'>Send it on WhatsApp</a>"
             f"<p class=muted style='margin-top:10px'><code style='word-break:break-all'>{esc(link)}</code></p></div>")
    body += f"<p class='rise muted'>We'll reach out in the WhatsApp group when it's time to vote. The round is always visible on the <a href='/b/{esc(parent['id']) if parent else esc(node['id'])}'>live board</a>.</p>"
    return page("Done", body)


HOME_POOL = os.environ.get("PB_HOME_POOL", "goa")   # the pool the front page shows; an alias or a node id
HOME_CSS = """<style>body{max-width:760px}.stage{display:inline-block;padding:7px 14px;border-radius:10px;background:var(--gold);color:var(--ink);font:400 11px/1 Silkscreen,"IBM Plex Mono",monospace;text-transform:uppercase;border:2px solid var(--ink)}
.big{font:400 40px/1.1 Newsreader,Georgia,serif;color:var(--ink)}p.lead{font-size:18px;line-height:1.45;text-align:left;color:var(--ink);margin:0 0 18px}
.budget{margin:14px 0 6px}.track{display:flex;height:22px;border:2px solid var(--ink);border-radius:4px;background:var(--paper);overflow:hidden}
.seg{display:block;height:100%}.seg+.seg{border-left:2px solid var(--card)}.paid{background:var(--ink)}.approved{background:var(--gold)}.asked{background:repeating-linear-gradient(135deg,var(--gold) 0 4px,transparent 4px 9px)}.left{background:var(--paper);border:1px solid var(--line)}
.legend{font:400 13px/1.6 "IBM Plex Mono",monospace;color:var(--muted);margin:8px 0 0;display:flex;flex-wrap:wrap;gap:4px 16px}.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:6px}
table{font:400 15px/1.4 "IBM Plex Mono",monospace}.proof{margin:14px 0 0;text-align:center}.how{margin-top:6px}.card.hero{margin:0 0 4px}h2.first{margin:30px 0 8px}.cta{margin:16px 0 0}button.primary{display:block;width:100%;margin:0;background:var(--gold);color:var(--ink);font-size:15px;padding:20px 22px;border:3px solid var(--ink);border-radius:12px;box-shadow:0 6px 0 var(--ink);cursor:pointer;transition:transform .08s,box-shadow .08s}button.primary span{margin-left:8px}button.primary:hover{transform:translateY(-1px);box-shadow:0 7px 0 var(--ink)}button.primary:active{transform:translateY(5px);box-shadow:0 1px 0 var(--ink)}button.primary:focus-visible{outline:3px solid var(--ink);outline-offset:3px}
.strip{list-style:none;padding:0;margin:8px 0 4px;display:grid;grid-template-columns:repeat(4,1fr);gap:10px}@media(max-width:560px){.strip{grid-template-columns:repeat(2,1fr)}}
.strip li{position:relative;border:2px solid var(--line);border-radius:4px;background:var(--card);padding:12px 10px 10px;font:400 14px/1.3 Newsreader,Georgia,serif;color:var(--muted)}
.strip svg{display:block;width:30px;height:30px;stroke:var(--muted);stroke-width:1.8;fill:none;stroke-linecap:round;stroke-linejoin:round;margin-bottom:8px}
.strip li.done{border-color:var(--ink);color:var(--ink)}.strip li.done svg{stroke:var(--ink)}
.strip li.now{border-color:var(--ink);background:var(--gold);color:var(--ink);box-shadow:3px 3px 0 var(--shadow)}.strip li.now svg{stroke:var(--ink)}
.strip em{position:absolute;top:8px;right:8px;font:400 9px/1 Silkscreen,'IBM Plex Mono',monospace;text-transform:uppercase;font-style:normal;color:var(--ink)}
.board{list-style:none;margin:0;padding:0;border:2px solid var(--ink);border-radius:4px;background:var(--card)}.board li{display:flex;align-items:center;gap:10px;padding:9px 12px;border-top:1px solid var(--line);margin:0}.board li:first-child{border-top:0}.board .n{flex:0 0 18px;font:400 11px/1 Silkscreen,'IBM Plex Mono',monospace;color:var(--muted)}.board li>div{flex:1 1 auto;min-width:0}.board b{display:block;font-size:17px;font-weight:500;line-height:1.25}.board .muted{display:block;font-size:13px;line-height:1.3;margin-top:1px}.board.sample b{color:var(--muted)}
.board .row-main{cursor:pointer}.board .row-main:focus{outline:3px solid var(--gold);outline-offset:2px;border-radius:3px}.board .row-main u{text-decoration:none}
.board .more{display:none;margin-top:8px;font-size:15px;line-height:1.45;color:var(--text)}.board .more p{margin:4px 0}.board li.open .more{display:block}.board li.open .row-main u{display:none}
.board .more a.btn.small{display:inline-block;width:auto;padding:10px 14px;margin-top:8px;font-size:10px}
.board li{align-items:flex-start}.board .thumb{margin-top:2px}.thumb{flex:0 0 auto;display:inline-flex;align-items:center;gap:6px;border:2px solid var(--ink);border-radius:999px;background:var(--paper);color:var(--ink);padding:6px 11px;text-decoration:none;font:500 14px 'IBM Plex Mono',monospace}.thumb svg{width:18px;height:18px;stroke:var(--ink);stroke-width:1.8;fill:none;stroke-linecap:round;stroke-linejoin:round}.thumb:hover{background:var(--gold)}.thumb:active{transform:translateY(1px)}
.post{display:block;border:2px solid var(--ink);border-radius:3px;background:var(--card);box-shadow:6px 6px 0 #20201dcc;padding:16px 18px;margin:14px 0;text-decoration:none;color:var(--ink)}.post .muted{display:block;font:400 13px 'IBM Plex Mono',monospace;margin-bottom:6px}.post b{display:block;font:500 22px/1.2 Newsreader,Georgia,serif}.post .blurb{display:block;font-size:16px;line-height:1.45;color:var(--muted);margin-top:6px}.post .more{display:inline-block;margin-top:10px;font:400 10px/1 Silkscreen,'IBM Plex Mono',monospace;text-transform:uppercase;border-bottom:2px solid var(--gold);padding-bottom:3px}.post:hover b{text-decoration:underline;text-decoration-color:var(--gold)}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{display:inline-block;border:1px solid var(--ink);border-radius:999px;padding:6px 12px;font-size:15px;color:var(--ink);background:var(--card)}
h2 .sum{font:400 13px 'IBM Plex Mono',monospace;color:var(--muted);letter-spacing:0}
.seals{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 8px}
.seal{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--ink);border-radius:999px;padding:4px 12px 4px 4px;font-size:15px;color:var(--ink);background:var(--card);box-shadow:2px 2px 0 var(--shadow);animation:rise .45s ease-out both}
.seal svg{width:24px;height:24px;flex:0 0 auto}.seal .ring{fill:none;stroke:var(--ink);stroke-width:2}.seal text{font:500 12px 'IBM Plex Mono',monospace;fill:var(--ink);text-anchor:middle;dominant-baseline:central}
.seal.v .ring{stroke:var(--gold);stroke-width:3.5}.seal.v{box-shadow:2px 2px 0 var(--gold)}.seal i{font-style:normal;color:var(--gold);font-weight:500;margin-left:2px}
@media(prefers-reduced-motion:reduce){.seal{animation:none}}</style>"""
NOTES_DIR = os.path.join(os.path.dirname(__file__), "notes")
NOTES = {"simulating-a-thousand-businesses": "Simulating a thousand businesses before the first real one"}
NOTE_META = {"simulating-a-thousand-businesses": ("30 October 2026", "Before the first real business registered, we put a thousand simulated ones through the real software and ran whole funding rounds. What we did, what broke, and the one thing no rule can fix.")}


@app.get("/notes/{slug}")
def note(slug: str):
    """A write-up, as a page in the site's look. The body is an HTML fragment in publicbank/notes/."""
    if slug not in NOTES:
        raise HTTPException(404)
    with open(os.path.join(NOTES_DIR, slug + ".html"), encoding="utf-8") as f:
        body = f.read()
    html = page(NOTES[slug], body).body.decode()
    return HTMLResponse(html.replace("</style>", "body{max-width:760px}h2{margin-top:34px}h3{font:500 20px/1.2 Newsreader,Georgia,serif;margin:24px 0 6px;color:var(--ink)}p.lead{font-size:21px;line-height:1.5;color:var(--ink)}ul{padding-left:22px}li{margin:8px 0}table{margin:12px 0}.chart{margin:18px 0 26px}.chart svg{width:100%;height:auto;display:block}.chart figcaption,.shot figcaption{font:500 15px/1.4 Newsreader,Georgia,serif;color:var(--ink);margin-bottom:8px}.chart .val{font:500 13px 'IBM Plex Mono',monospace;fill:var(--ink)}.chart .cat{font:400 13px 'IBM Plex Mono',monospace;fill:var(--muted)}.chart .axis{stroke:var(--line);stroke-width:1}.legend{font:400 13px 'IBM Plex Mono',monospace;color:var(--muted);margin:0 0 6px}.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:6px}.shot{margin:18px 0 26px}.shot .card{margin:10px 0}.btnlike{display:inline-block;font:400 10px/1 Silkscreen,'IBM Plex Mono',monospace;text-transform:uppercase;border:2px solid var(--ink);border-radius:10px;padding:8px 12px;margin:0 8px 0 0;background:var(--card);box-shadow:3px 3px 0 var(--shadow)}</style>", 1))


STEPS = [   # the round as a drawn sequence; which step is lit comes from the live round
    ("register", "Register", "M4 3h16v18H4z M8 8h8 M8 12h8 M8 16h5"),
    ("need", "Say what you need", "M4 5h16v10H9l-4 4v-4H4z"),
    ("drafts", "Drafts appear", "M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z M19 15l1 2 2 1-2 1-1 2-1-2-2-1 2-1z"),
    ("back", "Back or lead one", "M5 12l4 4 10-10"),
    ("points", "Split 100 points", "M6 8h2 M11 8h7 M6 12h5 M14 12h4 M6 16h8 M17 16h1"),
    ("approve", "Approve the plan", "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M8 12l3 3 5-6"),
    ("paid", "Fund pays the vendor", "M7 4h10 M7 8h10 M9 4c5 0 7 2 7 4s-2 4-7 4h-2l7 8"),
    ("record", "On the public record", "M3 20h18 M6 17V9 M11 17V5 M16 17v-7 M21 17v-4"),
]
STAGE_STEP = {"proposals": 3, "ballots": 4, "plans": 4, "ratifying": 5, "ratified": 6, "failed": 5, "paid": 7, "closed": 7}


def _steps_strip(stage: str | None, anyone_in: bool) -> str:
    now = STAGE_STEP.get(stage or "", 0) if anyone_in else 0
    out = ["<ol class=strip aria-label='How a round works'>"]
    for i, (k, label, path) in enumerate(STEPS):
        cls = "done" if i < now else "now" if i == now else ""
        out.append(f"<li class='{cls}'><svg viewBox='0 0 24 24' aria-hidden=true><path d='{path}'/></svg><span>{label}</span>" + ("<em>now</em>" if i == now else "") + "</li>")
    return "".join(out) + "</ol>"


@app.get("/")
def home():
    """The front page is the home pool itself, for a shop owner on a phone first: one sentence, proof it is real, a way
    in, the round drawn as steps with the live stage lit, what gets funded, who is in, where the money stands, the record."""
    home_id = _resolve(HOME_POOL)
    try:
        node = core.get_node(con, home_id)
    except core.PBError:
        node = None
    if node:
        rnd = core.current_round(con, node["id"])
        b = _budget(node, rnd)
        kids = sorted(core.children(con, node["id"]), key=lambda k: -k["created_at"])
        first = min((k["created_at"] for k in kids), default=None)
        proof = (f"{len(kids)} business{'es' if len(kids) != 1 else ''} in since {core._ist(first)[:10]}" if kids else "Opening now") + (f" · {STAGE_WORDS.get(b['stage'], b['stage'])}" if b["stage"] else "")
        cta = f"<form method=get action='/apply/{esc(HOME_POOL)}' class=cta><button class=primary>Register your business <span aria-hidden=true>→</span></button></form>"
        body = ("<p class=lead>A ₹5 lakh common fund for what Goan businesses buy and run together: a shared scooter, a trainer, one licence for "
                "several shops. Say what you'd share; the businesses vote; the fund pays the vendor.</p>")
        # the hero: the fund as a bar (paid, approved, asked for, remaining), the stage, and the button straight under it
        body += (f"<div class='card hero'><div class=row style='justify-content:space-between;flex-wrap:wrap;gap:8px'><span class=stage>{esc(STAGE_WORDS.get(b['stage'], b['stage'] or 'no round open'))}</span>"
                 f"<span class=big>{_rupees(b['pot'])}</span></div>{_budget_bar(b)}</div>")
        body += cta + f"<p class='muted proof'>{esc(proof)}</p>"
        # then who is in: a seal per business, so being in and accepted looks like something
        body += _seals(node, kids)
        body += commonsmap.map_section(node["id"])
        # then the funding proposals, every one, most backed first; a tap opens the proposal, the thumb leads into registering
        props = []
        if rnd and rnd["status"] == "proposals":
            props = db.rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND status IN ('eligible','draft') ORDER BY created_at DESC", (rnd["id"],)).fetchall())
            props.sort(key=lambda p: -len(db.j(p["cosign_nodes"], [])))
            # the leaderboard: what businesses put forward, plus twin drafts a business has taken the lead on; then ONE
            # twin draft nobody leads yet (the most backed), at the bottom, so the twin is seen without crowding people out
            backed = [p for p in props if p["source"] != "twin" or p.get("lead_id")]
            spare = [p for p in props if p not in backed]
            props = backed + spare[:1]
        body += "<h2 class=first>Funding proposals</h2>"
        if props:
            names = {k["id"]: k["name"] for k in kids}
            rows_html = ""
            for i, p in enumerate(props, 1):
                signs = [names.get(x, x.rsplit("/", 1)[-1]) for x in db.j(p["cosign_nodes"], [])]
                who = "Suggested from what businesses said they need" if p["source"] == "twin" else "Proposed by a business"
                pill = "<span class='tag twin'>AI twin</span> " if p["source"] == "twin" else ""
                sch = db.j(p.get("scheme_json"), None) if p.get("scheme_json") else None
                more = (f"<div class=more id='prop-{esc(p['id'])}'><p>{esc(p['text'] or '').replace(chr(10), '<br>')}</p>"
                        + (f"<p class=muted>A scheme could carry part of this: <b>{esc(sch['name'])}</b> ({esc(sch['share'])})" + (f" · <a href='{esc(sch['url'])}'>read it</a>" if sch.get("url") else "") + "</p>" if sch else "")
                        + f"<p class=muted>{who}" + (f" · backed by {esc(', '.join(signs))}" if signs else " · nobody backs it yet")
                        + (f" · at least {_rupees(p['min_paise'])}, ideally {_rupees(p['ideal_paise'])}" if p["min_paise"] else f" · ideally {_rupees(p['ideal_paise'])}") + "</p>"
                        f"<a class='btn small' href='/apply/{esc(HOME_POOL)}?for={esc(p['id'])}'>Back this with my business</a></div>")
                rows_html += (f"<li><span class=n>{i}</span><div class=row-main role=button tabindex=0 aria-expanded=false aria-controls='prop-{esc(p['id'])}'>{pill}<b>{esc(p['title'])}</b>"
                              f"<span class=muted>about {_rupees(p['ideal_paise'])}<u> · tap to read</u></span>{more}</div>"
                              f"<a class=thumb href='/apply/{esc(HOME_POOL)}?for={esc(p['id'])}' title='Back this: register your business'><svg viewBox='0 0 24 24' aria-hidden=true><path d='M7 11v9H4v-9z M7 11l4-7c2 0 3 1 3 3v3h5c1 0 2 1 2 2l-1.5 7c0 1-1 1-2 1H7'/></svg><span>{len(signs)}</span></a></li>")
            body += ("<ol class=board>" + rows_html + "</ol>"
                     "<script>(function(){document.querySelectorAll('.board .row-main').forEach(function(d){function go(){var li=d.closest('li'),o=li.classList.toggle('open');d.setAttribute('aria-expanded',o?'true':'false')}"
                     "d.addEventListener('click',function(e){if(e.target.closest('a'))return;go()});d.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();go()}})})})()</script>")
            note = (f"{len(backed)} proposal{'s' if len(backed) != 1 else ''} businesses have put forward or lead, most backed first" + (f", and one of the {len(spare)} the AI twin drafted from their needs that nobody leads yet" if spare else "")
                    + ". Tap one to read it; the number is how many businesses back it, and tapping that adds yours.")
        else:
            body += "<ol class='board sample'>" + "".join(f"<li><span class=n>{i}</span><div><b>{t}</b><span class=muted>about {r}</span></div></li>" for i, (t, r) in enumerate(
                [("A delivery scooter shared along a lane", "₹50,000"), ("One billing licence for several shops", "₹15,000"), ("A two-day trainer for counter staff", "₹20,000")], 1)) + "</ol>"
            note = "The kind of thing the fund buys: things several businesses use together."
        body += f"<p class='muted how'>{note}</p>"
        body += commonsmap.twin_section(node["id"])
        body += "<h2>How it works</h2>" + _steps_strip(b["stage"], bool(kids))
        body += "<h2>Where the money stands</h2><p class=muted>The fund pays vendors directly. Nothing moves until most of the businesses that vote approve the plan. The bar at the top is the live position.</p>"
        body += "<h2>Blog</h2>" + "".join(
            f"<a class=post href='/notes/{esc(k)}'><span class=muted>{esc(NOTE_META.get(k, ('', ''))[0])}</span><b>{esc(v)}</b><span class=blurb>{esc(NOTE_META.get(k, ('', ''))[1])}</span><span class=more>Read it →</span></a>"
            for k, v in NOTES.items())
        body += (f"<h2>For the record</h2><p><a href='/b/{esc(HOME_POOL)}'>The live board</a> · <a href='/export/{esc(node['id'])}'>the export</a> · "
                 f"<a href='/twin'>the digital twin of Goa's panchayats</a> · <a href='/privacy'>privacy</a></p>")
        body += cta
        html = page(node["name"], body).body.decode()
        return HTMLResponse(html.replace(CSS, CSS + HOME_CSS, 1).replace("<title>", "<meta http-equiv=refresh content=60><title>", 1))
    pools = db.rows(con.execute("SELECT * FROM nodes WHERE parent_id IS NULL ORDER BY created_at DESC").fetchall())
    body = ("<p>A treasury a community runs together. Say what you need, spread 100 points across the proposals, approve the "
            "plan, and watch every rupee on a public board. Nothing moves without the group's approval.</p>")
    for p in pools:
        n = len(core.children(con, p["id"]))
        body += (f"<div class=card><b>{esc(p['name'])}</b>" + (f" <span class=muted>· {n} {'businesses' if p.get('pool_vote') == 'nodes' else 'nodes'}</span>" if n else "")
                 + f"<br><a href='/b/{esc(p['id'])}'>Live board</a> · <a href='/apply/{esc(p['id'])}'>Register a business</a></div>")
    if not pools:
        body += "<p class=muted>Nothing is running here yet.</p>"
    body += "<h2>Notes</h2>" + "".join(f"<p><a href='/notes/{esc(k)}'>{esc(v)}</a></p>" for k, v in NOTES.items())
    body += "<h2>Digital twin</h2><p><a href='/twin'>Every local body in Goa, read against the government's schemes</a>: what the place is eligible for now, what it could plausibly become eligible for, and the step that gets it there.</p>"
    return page("Public Bank", body)


# ----------------------------------------------------------------------------
# the digital twin: every local body in Goa read against the scheme catalogue
# ----------------------------------------------------------------------------
from . import commonsmap, places, schemes, twinmap  # noqa: E402


@app.get("/commons/{pool_id:path}/map.json")
def commons_map(pool_id: str):
    """The Commons on the map: businesses at their places, the round's proposals and whom each connects."""
    pool_id = _resolve(pool_id)
    try:
        core.get_node(con, pool_id)
    except core.PBError:
        raise HTTPException(404)
    return JSONResponse(commonsmap.data(con, pool_id))

TWIN_CSS = ("<style>.tag.ok{background:#2f6b3f;color:#fbf6e9}.tag.maybe{background:var(--gold)}.tag.no{background:var(--card);border:1px solid var(--line);color:var(--muted)}"
            ".tag.draw{background:var(--ink);color:#fbf6e9}.lv a{margin-right:14px}.lv b{margin-right:14px}.twin .prop{padding:10px 0}.twin .why{display:block;color:var(--muted);font-size:15px;line-height:1.45;margin-top:3px}"
            ".twin .step{display:block;font-size:15px;color:var(--ink);margin-top:3px}.twin .step:before{content:'→ '}.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px 16px}"
            ".facts div{font:400 14px 'IBM Plex Mono',monospace;color:var(--muted)}.facts b{display:block;font:500 22px 'IBM Plex Mono',monospace;color:var(--ink)}.sum{font:400 15px 'IBM Plex Mono',monospace;color:var(--muted)}"
            "table.places td a{text-decoration:none;border-bottom:1px solid var(--line)}details.fold summary{font-weight:400;color:var(--muted)}</style>")
LEVELS = [("village_panchayat", "Village panchayats"), ("municipal", "Towns"), ("taluka", "Talukas"), ("district", "Districts"), ("state", "Goa")]
STATUS_WORDS = {"eligible": ("ok", "eligible"), "plausible": ("maybe", "plausible"), "unlikely": ("no", "unlikely")}


def _pct(x: float | None) -> str:
    return f"{x:.0%}" if x is not None else "?"


def _num(x) -> str:
    return f"{int(x):,}" if x is not None else "?"


def _places_at(level: str) -> list[dict]:
    if level == "municipal":
        return places.divisions("municipal_council") + places.divisions("municipal_corporation")
    if level in places.ROLLUPS:
        return places.rollups(level)
    return places.divisions("village_panchayat")


@app.get("/twin")
def twin_index(level: str = "village_panchayat"):
    if level not in dict(LEVELS):
        raise HTTPException(404)
    ds = sorted(_places_at(level), key=lambda d: (d.get("district") or "", d.get("taluka") or "", d["name"]))
    cat = schemes.load()
    body = ("<p>A digital twin of each local body in Goa: what the public figures say about the place, read against "
            f"{len(cat['schemes'])} government schemes. For every scheme the twin says whether the place is <span class='tag ok'>eligible</span> now, "
            "<span class='tag maybe'>plausible</span> with a step people there could take, or <span class='tag no'>unlikely</span>, and why.</p>"
            "<p class=lv>" + " ".join(f"<b>{esc(w)}</b>" if k == level else f"<a href='/twin?level={k}'>{esc(w)}</a>" for k, w in LEVELS) + "</p>")
    body += twinmap.section(heading="On the map")
    if not ds:
        body += "<p class=muted>No places loaded yet.</p>"
    else:
        body += "<table class=places><tr><th>Place</th><th>Taluka</th><th>Households</th><th>People</th><th>Farming</th><th>Coast</th><th>Eligible</th><th>Plausible</th><th>Unlikely</th><th>Running</th></tr>"
        for d in ds:
            p = places.profile(d); sm = places.summary_of(d["id"]) or {}
            coast = "yes" if p["coastal"] else ("—" if p["coastal"] is False else "?")
            farming = _pct(p["farmers"] / p["workers"]) if p.get("workers") and p.get("farmers") is not None else "?"
            body += (f"<tr><td><a href='/twin/{esc(d['id'])}'>{esc(d['name'])}</a></td><td class=muted>{esc(d.get('taluka') or (d.get('district') or ''))}</td>"
                     f"<td>{_num(p['households'])}</td><td>{_num(p['population'])}</td><td>{farming}</td><td>{coast}</td>"
                     f"<td>{sm.get('eligible', '')}</td><td>{sm.get('plausible', '')}</td><td class=muted>{sm.get('unlikely', '')}</td><td>{sm.get('drawing', '') or '—'}</td></tr>")
        body += "</table>"
    body += (f"<p class=muted>Figures: Census 2011 (village totals summed per panchayat) and whatever programme tables are published per panchayat. "
             f"Schemes: {esc(cat['caveat'])} The rules that read a scheme against a place are in the open; argue with the rule, not the answer.</p>")
    html = page("Digital twin", body).body.decode()
    return HTMLResponse(html.replace(CSS, CSS + TWIN_CSS, 1))


def _twin_row(s: dict, v: dict, members: dict | None, n_members: int) -> str:
    cls, word = STATUS_WORDS[v["status"]]
    tags = f"<span class='tag {cls}'>{word}</span>" + ("<span class='tag draw'>already running</span>" if v["drawing"] else "")
    if members is not None:
        m = members.get(s["id"], {})
        tags += f" <span class=sum>in {m.get('eligible', 0) + m.get('plausible', 0)} of {n_members}</span>"
    name = f"<a href='{esc(s['source_url'])}'>{esc(s['name'])}</a>" if s.get("source_url") else esc(s["name"])
    meta = esc(s["department"]) + (f" · up to {_rupees(s['max_rupees'] * 100)}" if s.get("max_rupees") else "") + (f" · {len(s['amendments'])} amendment{'s' if len(s['amendments']) != 1 else ''}" if s["amendments"] else "")
    for tg in s.get("tags", []):
        tags += f"<span class=tag>{esc(tg)}</span>"
    if s.get("portals"):
        meta += " · on " + ", ".join(f"<a href='{esc(p['url'])}'>{esc(p['portal'])}</a>" if p.get("url") else esc(p["portal"]) for p in s["portals"])
    if s.get("apply_online"):
        meta += f" · <a href='{esc(s['apply_online']['url'])}'>apply on Goa Online</a>"
    out = f"<div class=prop>{tags} <b>{name}</b> <span class=muted>· {meta}</span><span class=why>{esc('; '.join(v['why']))}</span>"
    for st in v["steps"]:
        out += f"<span class=step>{esc(st)}</span>"
    return out + "</div>"


@app.get("/twin/map.json")
def twin_map(v: str = ""):
    """The bodies, talukas and districts as simplified polygons for the tappable map. Cached an hour by the
    browser; the page passes the file's version as `v`, so a rebuilt map is fetched afresh."""
    if not twinmap.available():
        raise HTTPException(404)
    return JSONResponse(twinmap.load(), headers={"Cache-Control": "public, max-age=3600"})


@app.get("/twin/locate")
def twin_locate(lat: float, lon: float):
    """The local body a point falls in, for the registration form's 'Use my location'. The point is not kept."""
    d = places.locate(lat, lon)
    if not d:
        raise HTTPException(404)
    return JSONResponse({"id": d["id"], "name": d["name"], "taluka": d["taluka"], "label": f"{d['name']} · {d['taluka']}"})


@app.get("/twin/businesses.json")
def twin_businesses():
    """Verified businesses of the home pool, each with the local body it sits in: the map's markers. Names and
    places only."""
    try:
        pool = core.get_node(con, _resolve(HOME_POOL))
    except core.PBError:
        return JSONResponse({"pool": None, "businesses": []})
    biz = [b for b in core.verified_businesses(con, pool["id"]) if b["place_id"]]
    return JSONResponse({"pool": pool["id"], "businesses": [{"node_id": b["node_id"], "name": b["name"], "place_id": b["place_id"]} for b in biz]})


@app.get("/twin/{div_id}/card.json")
def twin_card(div_id: str):
    """What the map shows for one place: figures, counts, the three schemes worth its attention first."""
    d = places.get(div_id)
    if not d:
        raise HTTPException(404)
    c = places.card(d)
    if d["kind"] not in places.ROLLUPS:
        from . import businesses
        try:
            c["businesses_draw"] = businesses.place_draw(con, _resolve(HOME_POOL), d["id"])
        except core.PBError:
            c["businesses_draw"] = None
    return JSONResponse(c)


@app.get("/twin/{div_id}.json")
def twin_json(div_id: str):
    d = places.get(div_id)
    if not d:
        raise HTTPException(404)
    t = places.twin(d)
    return JSONResponse({"division": t["division"], "profile": t["profile"], "summary": t["summary"], "verdicts": t["verdicts"], "members": t["members"],
                         "caveat": schemes.load()["caveat"]})


@app.get("/twin/{div_id}")
def twin_page(div_id: str):
    d = places.get(div_id)
    if not d:
        raise HTTPException(404)
    t = places.twin(d); p = t["profile"]; sm = t["summary"]
    by_id = {s["id"]: s for s in schemes.schemes()}
    kind = places.KINDS.get(d["kind"], d["kind"])
    crumbs = [f"<a href='/twin'>Digital twin</a>"]
    if d.get("district") and d["kind"] != "district":
        crumbs.append(f"<a href='/twin/{places.slug('district-' + d['district'])}'>{esc(d['district'])}</a>")
    if d.get("taluka") and d["kind"] not in places.ROLLUPS:
        crumbs.append(f"<a href='/twin/{places.slug('taluka-' + d['taluka'])}'>{esc(d['taluka'])}</a>")
    body = f"<p class=muted>{' · '.join(crumbs)} · {esc(kind)}" + (f" · {d['wards']} wards" if d.get("wards") else "") + (f" · also written {esc(', '.join(d['aliases']))}" if d.get("aliases") else "") + "</p>"
    n_members = len(d.get("members", [])) if d["kind"] in places.ROLLUPS else 0
    if n_members:
        mk = d.get("member_kinds", {})
        body += "<p class=muted>" + ", ".join(f"{n} {places.KINDS.get(k, k).lower()}{'s' if n != 1 else ''}" for k, n in mk.items()) + "</p>"
    facts = [("Households", _num(p["households"])), ("People", _num(p["population"])), ("Literacy", _pct(p["literacy"])), ("In agriculture", _num(p["farmers"])),
             ("Coast", "yes" if p["coastal"] else ("no" if p["coastal"] is False else "?")), ("Tidal water", "yes" if p["estuarine"] else ("no" if p["estuarine"] is False else "?"))]
    if p.get("mgnrega_job_cards") is not None: facts.append(("MGNREGA job cards", _num(p["mgnrega_job_cards"])))
    if p.get("shgs") is not None: facts.append(("Self-help groups", _num(p["shgs"])))
    if p.get("fisher_households") is not None: facts.append(("Fishing households", _num(p["fisher_households"])))
    if p.get("dairy_societies") is not None: facts.append(("Dairy societies", _num(p["dairy_societies"])))
    if p.get("pmayg_houses") is not None: facts.append(("PMAY-G houses", _num(p["pmayg_houses"])))
    # the census categories the tribal and SC schemes turn on, last and small: a fact the rules use, not a headline
    facts += [("Scheduled Tribes", _pct(p["st_share"])), ("Scheduled Castes", _pct(p["sc_share"]))]
    body += "<div class=card><div class=facts>" + "".join(f"<div>{esc(k)}<b>{esc(v)}</b></div>" for k, v in facts) + "</div>"
    conf = {"mapped": "village totals mapped to this body", "partial": "some of this body's villages only", "name": "matched by name only", "none": "no census figures found"}.get(p["census_confidence"], p["census_confidence"])
    if d.get("census", {}).get("note"):
        conf += "; " + d["census"]["note"]
    if d.get("flags", {}).get("coastal_basis"):
        conf += ". Coast: " + d["flags"]["coastal_basis"]
    src = [f"Census 2011 ({conf})"] + [esc(x) for x in d.get("sources", []) if x]
    body += f"<p class=muted style='margin:12px 0 0'>Figures: {'; '.join(src)}. A question mark is a figure we do not hold yet.</p></div>"
    body += (f"<p class=sum><span class='tag ok'>eligible</span> {sm['eligible']} &nbsp; <span class='tag maybe'>plausible</span> {sm['plausible']} &nbsp; "
             f"<span class='tag no'>unlikely</span> {sm['unlikely']} &nbsp; <span class='tag draw'>already running</span> {sm['drawing']} &nbsp;· of {sm['schemes']} schemes</p>")
    order = {"eligible": 0, "plausible": 1, "unlikely": 2}
    groups = {"body": [], "groups": [], "people": []}
    for v in t["verdicts"]:
        groups[places.section(by_id[v["scheme_id"]])].append(v)
    heads = {"body": f"For the {kind.lower()} itself" if d["kind"] not in places.ROLLUPS else "For the local bodies here",
             "groups": "For groups, societies and organisations here", "people": "For households and people here"}
    for key in ("body", "groups", "people"):
        vs = sorted(groups[key], key=lambda v: (order[v["status"]], not v["drawing"], by_id[v["scheme_id"]]["name"]))
        if not vs:
            continue
        live = [v for v in vs if v["status"] != "unlikely"]; dead = [v for v in vs if v["status"] == "unlikely"]
        body += f"<h2>{esc(heads[key])} <span class=sum>· {len(live)}</span></h2><div class=twin>"
        body += "".join(_twin_row(by_id[v["scheme_id"]], v, t["members"], n_members) for v in live)
        if dead:
            body += f"<details class=fold><summary>{len(dead)} unlikely here</summary>" + "".join(_twin_row(by_id[v["scheme_id"]], v, t["members"], n_members) for v in dead) + "</details>"
        body += "</div>"
    cat = schemes.load()
    want = "Demand 55" if d["kind"].startswith("municipal") else "Demand 31" if d["kind"] == "village_panchayat" else None
    lines = [g for g in cat["grants"] if want is None or g["demand"].startswith(want)]
    if lines:
        body += "<h2>Budget lines that fund local bodies, 2026-27</h2><p class=muted>Line names are from the Demands for Grants; the amounts read from the PDFs did not reconcile with the official totals, so they are shown as pointers to check, not figures to quote.</p><table><tr><th>Line</th><th>Plan</th><th>Recipient</th><th>Rs lakh (unverified)</th></tr>"
        for g in lines:
            body += f"<tr><td><a href='{esc(g['source_url'])}'>{esc(g['line'])}</a></td><td class=muted>{esc(g['plan'])}</td><td class=muted>{esc(g['recipient'])}</td><td class=muted>{g['amount_lakh_unverified'] if g['amount_lakh_unverified'] is not None else ''}</td></tr>"
        body += "</table>"
    body += f"<p class=muted><a href='/twin/{esc(d['id'])}.json'>JSON</a> · {esc(cat['caveat'])}</p>"
    html = page(d["name"], body).body.decode()
    return HTMLResponse(html.replace(CSS, CSS + TWIN_CSS, 1))


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
