"""
WhatsApp adapter: the on-ramp and notification channel for nodes whose members live on WhatsApp
(Goan businesses, self-help groups, a ward), via the WhatsApp Business Platform (Meta's Cloud API).

Identity is the phone number WhatsApp verified. Nothing else is asked for. A member's first contact is a
tap on a click-to-chat link, https://wa.me/<our number>?text=JOIN%20<code>, which opens a chat with the
prefilled message; sending it is their opt-in and opens a 24-hour window in which we may reply freely.
Outside that window a notification must be an approved template (PB_WA_TEMPLATE, one body parameter).

  JOIN <code>   from an owner's card (a `join_coord` token: becomes the node's coordinator, is shown the
                parent's charter) or from a link the owner forwarded (a `join` token: joins as an active
                member straight away, because the owner shared it)
  JOIN          with no code and PB_WA_POOL set: registers a new business as a node under that pool
  STATUS        the member's nodes and stages, plus a one-tap link to the web page
  PROPOSE, VOTE, RANK, APPROVE   the round, as conversations; ballots and ranking also offer the web page
  LINK          coordinator: a fresh link to forward to staff
  NEED <text>   one thing the business needs (the twin drafts proposals from these); asked once after the charter
  COSIGN        coordinator: put the business behind a proposal in the pool's round
  CONTRIBUTE N  coordinator: record the business's ₹N co-contribution to the pool's round
  NEXT          coordinator: move a round to its next stage (what the web page's button does)
  PAYOUTS       treasurer: the queue, each item with a "Mark paid" button → reference
  MENU, CANCEL

Conversation state is a row in `wa_state` (any instance can continue it). Every step ends in the same core
call the web page and the Telegram bot make. Inbound messages are de-duplicated by WhatsApp message id.
Button and list ids carry what a Telegram callback would: `pl:y`, `bc:n`, `rk:<plan>`, `ra:<plan>:approve`…
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import urllib.parse

import httpx

from . import convo, core, db
from .convo import (NEXT_STEP, RULE_NAMES, STAGES, advanceable, ballot_prompt, coordinated, cosignable, err_text, parse_points, parse_rupees,
                    plan_lines, proposal_summary, receipt_line, rounds_at, rupees)

log = logging.getLogger("publicbank.wa")
BASE_URL = os.environ.get("PB_BASE_URL", "http://localhost:8000").rstrip("/")
GRAPH = os.environ.get("PB_WA_GRAPH", "https://graph.facebook.com/v21.0").rstrip("/")
TOKEN = os.environ.get("PB_WA_TOKEN", "")               # Cloud API access token
PHONE_ID = os.environ.get("PB_WA_PHONE_ID", "")         # the phone number id the API sends from
NUMBER = re.sub(r"\D", "", os.environ.get("PB_WA_NUMBER", ""))   # that number as digits, for wa.me links
VERIFY_TOKEN = os.environ.get("PB_WA_VERIFY_TOKEN", "")  # webhook subscription handshake
APP_SECRET = os.environ.get("PB_WA_APP_SECRET", "")     # signs every webhook body (X-Hub-Signature-256)
POOL = os.environ.get("PB_WA_POOL", "")                  # node id a self-registered business joins under
TEMPLATE = os.environ.get("PB_WA_TEMPLATE", "")          # approved template name with one {{1}} body parameter
CHARTER = convo.CHARTER
WINDOW = 24 * 3600
STATE_TTL = 24 * 3600
con = db.connect()


# ----------------------------------------------------------------------------
# sending
# ----------------------------------------------------------------------------
class GraphSender:
    """The Cloud API. Tests replace `sender` with a fake that records calls."""

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=15)

    def _post(self, payload: dict) -> dict:
        r = self.client.post(f"{GRAPH}/{PHONE_ID}/messages", headers={"Authorization": f"Bearer {TOKEN}"},
                             json={"messaging_product": "whatsapp", "recipient_type": "individual", **payload})
        r.raise_for_status()
        return r.json()

    def text(self, to: str, body: str) -> dict:
        return self._post({"to": to, "type": "text", "text": {"body": body[:4096], "preview_url": False}})

    def buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> dict:
        """Up to three reply buttons: [(id, title)]. Titles are cut at 20 characters, the platform's limit."""
        return self._post({"to": to, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": body[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": i[:256], "title": t[:20]}} for i, t in buttons[:3]]}}})

    def list(self, to: str, body: str, button: str, rows: list[tuple[str, str, str]]) -> dict:
        """A list message: up to ten rows of (id, title, description)."""
        return self._post({"to": to, "type": "interactive", "interactive": {
            "type": "list", "body": {"text": body[:1024]},
            "action": {"button": button[:20], "sections": [{"title": "Options", "rows": [
                {"id": i[:200], "title": t[:24], "description": (d or "")[:72]} for i, t, d in rows[:10]]}]}}})

    def template(self, to: str, name: str, params: list[str]) -> dict:
        return self._post({"to": to, "type": "template", "template": {
            "name": name, "language": {"code": "en"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": p[:1024]} for p in params]}]}})


sender = GraphSender()


def send(phone: str, text: str) -> bool:
    """A notification: free text inside the member's 24-hour window, else the template. False if nothing could go."""
    m = member_for(phone)
    inside = bool(m and m.get("wa_last_in_at") and m["wa_last_in_at"] > db.now() - WINDOW)
    try:
        if inside or not TEMPLATE:
            sender.text(phone, text)
        else:
            sender.template(phone, TEMPLATE, [text])
        return True
    except Exception as e:  # outside the window with no template, a bad number, a Graph outage
        log.info("wa send to %s… skipped: %s", phone[:5], type(e).__name__)
        return False


def notify_node(node_id: str, text: str) -> int:
    """Message every active member of a node who joined by WhatsApp. Returns how many were reached."""
    return sum(send(m["wa_phone"], text) for m in core.active_members(con, node_id) if m.get("wa_phone"))


def join_link(token: str) -> str:
    if NUMBER:
        return f"https://wa.me/{NUMBER}?text={urllib.parse.quote('JOIN ' + token)}"
    return f"{BASE_URL}/j/{token}"


# ----------------------------------------------------------------------------
# webhook
# ----------------------------------------------------------------------------
def verify_signature(body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256 over the raw body with the app secret. With no secret configured, nothing is checked."""
    if not APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    mac = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, header[7:])


def _first_time(key: str) -> bool:
    if con.execute("SELECT 1 FROM idempotency WHERE key=?", (key,)).fetchone():
        return False
    with db.tx(con):
        con.execute("INSERT OR IGNORE INTO idempotency (key,response,created_at) VALUES (?,?,?)", (key, "{}", db.now()))
    return True


def handle_update(payload: dict) -> None:
    """One webhook body: zero or more messages. Statuses (delivered, read) are ignored."""
    for entry in payload.get("entry") or []:
        for ch in entry.get("changes") or []:
            v = ch.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in v.get("contacts") or []}
            for msg in v.get("messages") or []:
                mid = msg.get("id") or ""
                if mid and not _first_time("wa:" + mid):
                    continue
                phone = re.sub(r"\D", "", str(msg.get("from") or ""))
                if not phone:
                    continue
                try:
                    handle_message(phone, names.get(phone), msg)
                except Exception:
                    log.exception("wa message %s", mid)


def handle_message(phone: str, name: str | None, msg: dict) -> None:
    m = member_for(phone)
    if m:
        touch(m)
    t = msg.get("type")
    if t == "text":
        on_text(phone, name, m, ((msg.get("text") or {}).get("body") or "").strip())
    elif t == "interactive":
        i = msg.get("interactive") or {}
        r = i.get("button_reply") or i.get("list_reply") or {}
        on_button(phone, name, m, r.get("id") or "")
    else:
        menu(phone, m)


# ----------------------------------------------------------------------------
# members and state
# ----------------------------------------------------------------------------
def member_for(phone: str) -> dict | None:
    return db.row(con.execute("SELECT * FROM members WHERE wa_phone=?", (phone,)).fetchone())


def touch(m: dict) -> None:
    with db.tx(con):
        con.execute("UPDATE members SET wa_last_in_at=?, wa_opt_in_at=COALESCE(wa_opt_in_at,?) WHERE id=?", (db.now(), db.now(), m["id"]))


def ensure_member(phone: str, name: str | None, m: dict | None) -> dict:
    if m:
        if name and not m.get("display"):
            with db.tx(con):
                con.execute("UPDATE members SET display=? WHERE id=?", (name[:80], m["id"]))
        return m
    m = core.upsert_member(con, verified_by="whatsapp", wa_phone=phone, display=(name or "")[:80] or None)
    touch(m)
    return m


def coordinators(node_id: str) -> list[dict]:
    return db.rows(con.execute("SELECT m.* FROM members m JOIN memberships ms ON ms.member_id=m.id WHERE ms.node_id=? AND ms.role IN ('coordinator','treasurer') AND ms.status='active'", (node_id,)).fetchall())


def is_treasurer(m: dict) -> bool:
    return bool(db.row(con.execute("SELECT 1 FROM memberships WHERE member_id=? AND role='treasurer' AND status='active'", (m["id"],)).fetchone()))


def get_state(phone: str) -> tuple[str | None, dict]:
    r = db.row(con.execute("SELECT * FROM wa_state WHERE phone=?", (phone,)).fetchone())
    if not r or r["updated_at"] < db.now() - STATE_TTL:
        return None, {}
    return r["state"], db.j(r["data"], {})


def set_state(phone: str, state: str, data: dict) -> None:
    with db.tx(con):
        con.execute("""INSERT INTO wa_state (phone,state,data,updated_at) VALUES (?,?,?,?)
                       ON CONFLICT(phone) DO UPDATE SET state=excluded.state, data=excluded.data, updated_at=excluded.updated_at""",
                    (phone, state, json.dumps(data), db.now()))


def clear_state(phone: str) -> None:
    with db.tx(con):
        con.execute("DELETE FROM wa_state WHERE phone=?", (phone,))


def login_url(m: dict, node_id: str | None = None) -> str:
    tok = core.mint_token(con, "login", member_id=m["id"])
    return f"{BASE_URL}/l/{tok}" + (f"?next=/n/{node_id}" if node_id else "")


# ----------------------------------------------------------------------------
# text and button dispatch
# ----------------------------------------------------------------------------
COMMANDS = {"propose": "propose", "vote": "ballot", "ballot": "ballot", "rank": "rank", "approve": "approve"}


def on_text(phone: str, name: str | None, m: dict | None, text: str) -> None:
    word, _, rest = text.partition(" ")
    w = word.lower().strip()
    if w == "join":
        return do_join(phone, name, m, rest.strip())
    if w in ("cancel", "stop"):
        clear_state(phone); sender.text(phone, "Dropped. Nothing was saved."); return
    if w in ("menu", "help", "hi", "hello", "start", "?"):
        return menu(phone, m)
    if w == "status":
        return status(phone, m)
    if w == "link":
        return staff_link(phone, m)
    if w == "payouts":
        return payouts(phone, m)
    if w == "need":
        return need(phone, m, rest.strip())
    if w == "cosign":
        return cosign(phone, m)
    if w in ("contribute", "pledge"):
        return contribute(phone, m, rest.strip())
    if w == "next":
        return nxt(phone, m)
    if w in COMMANDS:
        return begin(phone, m, COMMANDS[w])
    state, d = get_state(phone)
    if not state:
        return menu(phone, m)
    if state == "j_name":
        return finish_self_join(phone, name, m, text)
    if not m:
        return menu(phone, m)
    if state == "n_text":
        clear_state(phone)
        if text.lower() == "skip":
            sender.text(phone, "No problem. Reply NEED and a line any time."); return
        return need(phone, m, text, d.get("node_id"))
    if state == "t_ref":
        clear_state(phone)
        core.mark_paid(con, d["payout_id"], text)
        sender.text(phone, "Recorded. It's on the ledger.")
    elif state.startswith("p_") and state not in ("p_lead", "p_confirm"):
        propose_text(phone, m, state, d, text)
    elif state == "b_points":
        ballot_text(phone, m, d, text)
    else:
        sender.text(phone, "Use the buttons above, or reply CANCEL.")


def on_button(phone: str, name: str | None, m: dict | None, bid: str) -> None:
    key, _, val = bid.partition(":")
    if key == "cmd":
        return on_text(phone, name, m, val)
    if not m:
        return menu(phone, m)
    if key == "ch":
        node_id, _, ans = val.rpartition(":")
        return charter_answer(phone, m, node_id, ans)
    if key == "go":
        cmd, _, node_id = val.partition(":")
        if cmd in STAGES: begin(phone, m, cmd, node_id)
        return
    if key == "ra":
        plan_id, _, verdict = val.partition(":")
        return approve_button(phone, m, plan_id, verdict)
    if key == "paid":
        if not is_treasurer(m): return
        set_state(phone, "t_ref", {"payout_id": val})
        sender.text(phone, "Reply with the UPI/IMPS reference number."); return
    if key == "cs":
        return cosign_pick(phone, m, val)
    if key == "nx":
        return nxt(phone, m, val)
    state, d = get_state(phone)
    expects = {"pl": "p_lead", "pc": "p_confirm", "bc": "b_confirm", "rk": "r_pick", "rc": "r_confirm"}
    if not state or expects.get(key) != state:
        sender.text(phone, "That question has expired. Start again: reply PROPOSE, VOTE, RANK or APPROVE."); return
    if key in ("pl", "pc"): propose_button(phone, m, key, val, d)
    elif key == "bc": ballot_button(phone, m, val, d)
    else: rank_button(phone, m, key, val, d)


def menu(phone: str, m: dict | None) -> None:
    if not m:
        sender.text(phone, "Hi. Tap the join link your coordinator shared, or scan your business's card."
                    + (" To register a new business, reply JOIN." if POOL else "")); return
    rows = [("cmd:status", "Status", "Where each round is"), ("cmd:propose", "Propose", "Put something on the list"),
            ("cmd:vote", "Vote", "Split 100 points"), ("cmd:rank", "Rank", "Order the plans"), ("cmd:approve", "Approve", "Approve or reject the plan")]
    rows.append(("cmd:need", "Need", "One thing your business needs"))
    if db.row(con.execute("SELECT 1 FROM memberships WHERE member_id=? AND role='coordinator' AND status='active'", (m["id"],)).fetchone()):
        rows.append(("cmd:link", "Team link", "A link to forward to your team"))
        if cosignable(con, m["id"]): rows.append(("cmd:cosign", "Co-sign", "Back a proposal in the pool"))
    if advanceable(con, m["id"]): rows.append(("cmd:next", "Next stage", "Move your round on"))
    if is_treasurer(m):
        rows.append(("cmd:payouts", "Payouts", "What's waiting to be paid"))
    sender.list(phone, "What would you like to do?", "Choose", rows)


# ----------------------------------------------------------------------------
# joining
# ----------------------------------------------------------------------------
def do_join(phone: str, name: str | None, m: dict | None, code: str) -> None:
    if code:
        tok = None
        for kind in ("join_coord", "join_team", "join"):
            try:
                tok = core.read_token(con, code, kind); break
            except core.PBError:
                pass
        if not tok:
            sender.text(phone, "That link has expired. Ask your coordinator for a fresh one."); return
        m = ensure_member(phone, name, m)
        node = core.get_node(con, tok["node_id"])
        if tok["kind"] == "join_coord":
            core.join_node(con, node["id"], m["id"], role="coordinator", auto_active=True)
            core.claim_application(con, node["id"], m["id"])   # a business that registered on the web form
            sender.text(phone, f"You're the coordinator for {node['name']}. You decide who's in, and you freeze the list and move the round along.")
            if node.get("parent_id") and not node.get("charter_at"):
                ask_charter(phone, node)
            else:
                send_staff_link(phone, node)
            return
        if core.is_role(con, node["id"], m["id"], ("coordinator", "treasurer")):
            send_staff_link(phone, node); return   # the owner tapped their own team link: don't demote them
        core.join_node(con, node["id"], m["id"], auto_active=True)   # active at once: the owner shared the link
        sender.text(phone, f"You're in: {node['name']}. I'll message you here when there's something to do. Reply STATUS any time.")
        for c in coordinators(node["id"]):
            if c.get("wa_phone") and c["wa_phone"] != phone:
                send(c["wa_phone"], f"{name or 'Someone'} joined {node['name']}.")
        return
    if not POOL:
        sender.text(phone, "Ask your coordinator for a join link, or scan the poster."); return
    try:
        core.get_node(con, POOL)
    except core.PBError:
        sender.text(phone, "Registration isn't open yet. Ask your coordinator for a join link."); return
    set_state(phone, "j_name", {})
    sender.text(phone, "Welcome. What's your business called? (This becomes your node's name.)")




def finish_self_join(phone: str, name: str | None, m: dict | None, text: str) -> None:
    biz = re.sub(r"\s+", " ", text).strip()[:80]
    if len(biz) < 2:
        sender.text(phone, "Send the business's name."); return
    m = ensure_member(phone, name, m)
    slug = re.sub(r"[^a-z0-9]+", "-", biz.lower()).strip("-") or "business"
    node = core.create_node(con, core.unique_node_id(con, f"{POOL}/{slug}"), biz, kind="group", parent_id=POOL)
    core.join_node(con, node["id"], m["id"], role="coordinator", auto_active=True)
    clear_state(phone)
    sender.text(phone, f"Created {biz}. You're its coordinator.")
    ask_charter(phone, node)


def ask_charter(phone: str, node: dict) -> None:
    parent = core.get_node(con, node["parent_id"])
    sender.buttons(phone, f"{parent['name']} works like this:\n\n{CHARTER}\n\nJoin {parent['name']} with {node['name']}?",
                   [(f"ch:{node['id']}:y", "Accept"), (f"ch:{node['id']}:n", "Not now")])


def charter_answer(phone: str, m: dict, node_id: str, ans: str) -> None:
    if not core.is_role(con, node_id, m["id"], ("coordinator",)):
        return
    node = core.get_node(con, node_id)
    if ans != "y":
        sender.text(phone, "No problem. Reply LINK when you're ready and I'll ask again."); return
    with db.tx(con):
        con.execute("UPDATE nodes SET charter_at=COALESCE(charter_at,?) WHERE id=?", (db.now(), node_id))
    sender.text(phone, f"Accepted. {node['name']} is in {core.get_node(con, node['parent_id'])['name']}.")
    send_staff_link(phone, node)
    if core.has_need(con, node_id):   # the form already asked
        return
    set_state(phone, "n_text", {"node_id": node_id})
    sender.text(phone, "One more thing: what's one thing your business needs right now? One line. Similar needs across businesses become shared proposals. (Reply SKIP to pass.)")


def send_staff_link(phone: str, node: dict) -> None:
    tok = core.mint_token(con, "join", node_id=node["id"], ttl=core.TOKEN_TTL_JOIN)
    sender.text(phone, f"Share this with your team so they can vote too:\n{join_link(tok)}\n\n"
                       f"Anyone who taps it joins {node['name']} straight away. It works for a week; reply LINK for a fresh one.")


def staff_link(phone: str, m: dict | None) -> None:
    if not m:
        return menu(phone, m)
    mine = db.rows(con.execute("SELECT n.* FROM nodes n JOIN memberships ms ON ms.node_id=n.id WHERE ms.member_id=? AND ms.role='coordinator' AND ms.status='active' ORDER BY n.id", (m["id"],)).fetchall())
    if not mine:
        sender.text(phone, "Only a coordinator can make team links. Ask yours."); return
    for node in mine:
        if node.get("parent_id") and not node.get("charter_at"):
            ask_charter(phone, node)
        else:
            send_staff_link(phone, node)


# ----------------------------------------------------------------------------
# status and the round
# ----------------------------------------------------------------------------
def status(phone: str, m: dict | None) -> None:
    if not m:
        return menu(phone, m)
    out = []
    for x in db.rows(con.execute("SELECT node_id FROM memberships WHERE member_id=? AND status='active' ORDER BY node_id", (m["id"],)).fetchall()):
        node = core.get_node(con, x["node_id"])
        rnd = core.current_round(con, x["node_id"])
        if not rnd:
            out.append(f"{node['name']} · no round open"); continue
        out.append(f"{node['name']} · {rnd['status']} · budget {rupees(core.spendable_budget(con, rnd))}")
        for p in core.eligible_proposals(con, rnd["id"]):
            out.append(f"  – {p['title']} (ideal {rupees(p['ideal_paise'])})")
        rec = receipt_line(con, rnd, m["id"])
        if rec:
            out.append("  your share went: " + rec)
    sender.text(phone, ("\n".join(out) or "Nothing open right now.") + f"\n\nOpen: {login_url(m)}")


def begin(phone: str, m: dict | None, cmd: str, node_id: str | None = None) -> None:
    if not m:
        return menu(phone, m)
    stage, verb = STAGES[cmd]
    found = [(n, r) for n, r in rounds_at(con, m["id"], stage) if node_id in (None, n["id"])]
    if cmd == "ballot" and found and not any(core.can_ballot(con, r, m["id"]) for _, r in found):
        sender.text(phone, err_text(core.PBError("node_votes_by_coordinator"))); return
    found = [(n, r) for n, r in found if cmd != "ballot" or core.can_ballot(con, r, m["id"])]
    if not found:
        sender.text(phone, f"Nothing to {verb} right now. Reply STATUS to see where each round is."); return
    if len(found) > 1:
        sender.list(phone, "Which one?", "Choose", [(f"go:{cmd}:{n['id']}", n["name"], "") for n, _ in found]); return
    node, rnd = found[0]
    {"propose": start_propose, "ballot": start_ballot, "rank": start_rank, "approve": start_approve}[cmd](phone, m, node, rnd)


# --- propose ----------------------------------------------------------------
def start_propose(phone: str, m: dict, node: dict, rnd: dict) -> None:
    set_state(phone, "p_title", {"round_id": rnd["id"], "node": node["name"]})
    sender.text(phone, f"A proposal for {node['name']}. What's it called? (Reply CANCEL to stop.)")


def propose_text(phone: str, m: dict, state: str, d: dict, text: str) -> None:
    if state == "p_title":
        if not text: sender.text(phone, "Send a short title."); return
        d["title"] = text[:120]; set_state(phone, "p_text", d)
        sender.text(phone, "Describe it in a few lines: what it is, who it's for, what the money buys.")
    elif state == "p_text":
        d["text"] = text[:2000]; set_state(phone, "p_ideal", d)
        sender.text(phone, "Ideal amount, in rupees?")
    elif state == "p_ideal":
        v = parse_rupees(text)
        if not v: sender.text(phone, "A whole number of rupees above 0, e.g. 5000."); return
        d["ideal"] = v; set_state(phone, "p_min", d)
        sender.text(phone, "The least that would still make it worth doing, in rupees? Send 0 if any amount helps.")
    elif state == "p_min":
        v = parse_rupees(text)
        if v is None or v > d["ideal"]: sender.text(phone, f"A whole number of rupees from 0 to {d['ideal']}."); return
        d["min"] = v; set_state(phone, "p_lead", d)
        sender.buttons(phone, "Will you lead it? The lead buys or organises it and is reimbursed against receipts.",
                       [("pl:y", "Yes, I'll lead"), ("pl:n", "No")])
    elif state == "p_upi":
        if text.lower() != "skip":
            if not re.fullmatch(r"[\w.\-]{2,256}@[\w.\-]{2,64}", text):
                sender.text(phone, "That doesn't look like a UPI id (name@bank). Or send skip."); return
            d["upi"] = text
        set_state(phone, "p_confirm", d)
        sender.buttons(phone, proposal_summary(d), [("pc:y", "Submit"), ("pc:n", "Cancel")])


def propose_button(phone: str, m: dict, key: str, ans: str, d: dict) -> None:
    if key == "pl":
        d["lead"] = ans == "y"
        if d["lead"] and not m.get("upi_id"):
            set_state(phone, "p_upi", d)
            sender.text(phone, "Your UPI id, for the reimbursement (e.g. name@okaxis)? Only the treasurer sees it. Send skip to add it later."); return
        set_state(phone, "p_confirm", d)
        sender.buttons(phone, proposal_summary(d), [("pc:y", "Submit"), ("pc:n", "Cancel")])
    elif key == "pc":
        clear_state(phone)
        if ans != "y":
            sender.text(phone, "Dropped. Nothing was saved."); return
        if d.get("upi"):
            with db.tx(con): con.execute("UPDATE members SET upi_id=? WHERE id=?", (d["upi"], m["id"]))
        try:
            p = core.add_proposal(con, d["round_id"], m["id"], title=d["title"], text=d["text"], ideal_paise=d["ideal"] * 100,
                                  min_paise=d["min"] * 100, lead_id=m["id"] if d["lead"] else None)
        except core.PBError as e:
            sender.text(phone, err_text(e)); return
        sender.text(phone, "Submitted. It's on the list." if p["status"] == "eligible" else "Saved as a draft. It reaches the ballot once someone leads it.")


# --- ballot -----------------------------------------------------------------
def start_ballot(phone: str, m: dict, node: dict, rnd: dict) -> None:
    cur = db.j((db.row(con.execute("SELECT points FROM ballots WHERE round_id=? AND member_id=?", (rnd["id"], m["id"])).fetchone()) or {}).get("points"), {})
    text, ids, titles = ballot_prompt(con, node, rnd, cur)
    set_state(phone, "b_points", {"round_id": rnd["id"], "ids": ids, "titles": titles})
    sender.text(phone, text + f"\n\nOr do it on the page: {login_url(m, node['id'])}")


def ballot_text(phone: str, m: dict, d: dict, text: str) -> None:
    pts = parse_points(text, d["ids"])
    if isinstance(pts, str):
        sender.text(phone, pts + " Try again, or reply CANCEL."); return
    d["points"] = pts; set_state(phone, "b_confirm", d)
    names = dict(zip(d["ids"], d["titles"])) | {"_unspent": "left unspent"}
    sender.buttons(phone, "Your ballot:\n" + "\n".join(f"{v:>3}  {names[k]}" for k, v in pts.items()), [("bc:y", "Submit"), ("bc:n", "Cancel")])


def ballot_button(phone: str, m: dict, ans: str, d: dict) -> None:
    clear_state(phone)
    if ans != "y":
        sender.text(phone, "Dropped. Nothing was saved."); return
    try:
        b = core.submit_ballot(con, d["round_id"], m["id"], d["points"])
    except core.PBError as e:
        sender.text(phone, err_text(e)); return
    sender.text(phone, "Ballot saved." + (" (This replaces your earlier one.)" if b["revision"] > 1 else "") + " Reply VOTE to change it until ballots close.")


# --- rank -------------------------------------------------------------------
def start_rank(phone: str, m: dict, node: dict, rnd: dict) -> None:
    plans = core.plans_of(con, rnd["id"])
    lines = [f"{node['name']}: four ways to spend {rupees(core.spendable_budget(con, rnd))}."]
    for pl in plans:
        lines += [f"\n{RULE_NAMES.get(pl['rule'], pl['rule'])}" + ("" if pl["valid"] else " (not valid)")] + plan_lines(con, rnd, pl)
    rec = receipt_line(con, rnd, m["id"])
    if rec:
        lines.append("\nUnder Equal Shares your share went to: " + rec)
    lines.append(f"\nSee it laid out: {login_url(m, node['id'])}")
    d = {"round_id": rnd["id"], "plans": {pl["id"]: RULE_NAMES.get(pl["rule"], pl["rule"]) for pl in plans}, "order": []}
    set_state(phone, "r_pick", d)
    sender.text(phone, "\n".join(lines))
    sender.list(phone, "Which plan is best?", "Pick", rank_rows(d))


def rank_rows(d: dict) -> list[tuple[str, str, str]]:
    return [(f"rk:{pid}", name, "") for pid, name in d["plans"].items() if pid not in d["order"]]


def rank_button(phone: str, m: dict, key: str, val: str, d: dict) -> None:
    if key == "rk":
        if val not in d["plans"] or val in d["order"]: return
        d["order"].append(val)
        so_far = "\n".join(f"{i}. {d['plans'][p]}" for i, p in enumerate(d["order"], 1))
        if len(d["order"]) < len(d["plans"]):
            set_state(phone, "r_pick", d)
            sender.list(phone, f"{so_far}\n\nNext?", "Pick", rank_rows(d)); return
        set_state(phone, "r_confirm", d)
        sender.buttons(phone, f"Your ranking:\n{so_far}", [("rc:y", "Submit"), ("rc:n", "Start over")])
    elif key == "rc":
        if val != "y":
            d["order"] = []; set_state(phone, "r_pick", d)
            sender.list(phone, "From the top: which plan is best?", "Pick", rank_rows(d)); return
        clear_state(phone)
        try:
            core.submit_ranking(con, d["round_id"], m["id"], d["order"])
        except core.PBError as e:
            sender.text(phone, err_text(e)); return
        sender.text(phone, "Ranking saved. Reply RANK to change it until the winner is picked.")


# --- approve ----------------------------------------------------------------
def start_approve(phone: str, m: dict, node: dict, rnd: dict) -> None:
    plan = db.row(con.execute("SELECT * FROM plans WHERE id=?", (rnd["winner_plan_id"],)).fetchone())
    eligible = len(core.active_members(con, node["id"]))
    need = -(-node["ratify_pct"] * eligible // 100)
    text = (f"{node['name']}: the winning plan is {RULE_NAMES.get(plan['rule'], plan['rule'])}.\n" + "\n".join(plan_lines(con, rnd, plan))
            + f"\n\nIt is funded only if {need} of {eligible} members ({node['ratify_pct']}%) approve. Not answering counts as not approving.")
    sender.buttons(phone, text, [(f"ra:{plan['id']}:approve", "Approve"), (f"ra:{plan['id']}:reject", "Reject")])


def approve_button(phone: str, m: dict, plan_id: str, verdict: str) -> None:
    rnd = db.row(con.execute("SELECT r.* FROM rounds r JOIN plans p ON p.round_id=r.id WHERE p.id=?", (plan_id,)).fetchone())
    if not rnd or rnd["winner_plan_id"] != plan_id:
        sender.text(phone, "That plan is no longer the one up for approval. Reply APPROVE again."); return
    try:
        core.submit_ratification(con, rnd["id"], m["id"], plan_id, verdict)
    except core.PBError as e:
        sender.text(phone, err_text(e)); return
    sender.text(phone, ("Approved." if verdict == "approve" else "Rejected.") + " Reply APPROVE to change your answer until approval closes.")


# --- needs, co-signing, contributions, next ----------------------------------
def need(phone: str, m: dict | None, text: str, node_id: str | None = None) -> None:
    if not m:
        return menu(phone, m)
    if not text:
        mine = db.rows(con.execute("SELECT node_id FROM memberships WHERE member_id=? AND status='active' ORDER BY node_id", (m["id"],)).fetchall())
        if not mine:
            return menu(phone, m)
        set_state(phone, "n_text", {"node_id": mine[0]["node_id"]})
        sender.text(phone, "What's one thing your business needs? One line."); return
    if not node_id:
        r = db.row(con.execute("SELECT node_id FROM memberships WHERE member_id=? AND status='active' ORDER BY node_id LIMIT 1", (m["id"],)).fetchone())
        if not r:
            return menu(phone, m)
        node_id = r["node_id"]
    try:
        core.add_need(con, node_id, m["id"], text)
    except core.PBError as e:
        sender.text(phone, err_text(e)); return
    sender.text(phone, "Noted. When the round opens, similar needs across businesses show up as draft proposals you can lead or back.")


def cosign(phone: str, m: dict | None) -> None:
    if not m:
        return menu(phone, m)
    opts = cosignable(con, m["id"])
    if not opts:
        sender.text(phone, "Nothing to co-sign right now: either no pool round is taking proposals, or your business has backed everything on the list."); return
    pool, rnd, props, _node = opts[0]
    sender.list(phone, f"{pool['name']}: which of these does your business want? Once {pool.get('cosign_min') or 1} businesses back one, it goes to the vote.", "Pick",
                [(f"cs:{p['id']}", p["title"], ("for you · " if p.get("suggested") else "") + f"ideal {rupees(p['ideal_paise'])}" + ("" if p["lead_id"] else " · no lead yet")) for p in props])


def cosign_pick(phone: str, m: dict, pid: str) -> None:
    try:
        p = core.get_proposal(con, pid)
        rnd = core.get_round(con, p["round_id"])
        via = core.require_in_tree(con, rnd["node_id"], m["id"])
        p = core.cosign_proposal(con, pid, via["id"], m["id"])
    except core.PBError as e:
        sender.text(phone, err_text(e)); return
    n = len(db.j(p["cosign_nodes"], []))
    need_n = core.get_node(con, rnd["node_id"]).get("cosign_min") or 0
    sender.text(phone, f"Co-signed. \"{p['title']}\" is backed by {n} node{'s' if n != 1 else ''}" + (f" of the {need_n} it needs" if need_n else "")
                + (", and it's on the ballot." if p["status"] == "eligible" else (". It still needs a lead." if not p["lead_id"] else ".")))


def contribute(phone: str, m: dict | None, amount: str) -> None:
    if not m:
        return menu(phone, m)
    mine = [n for n in coordinated(con, m["id"]) if n.get("parent_id")]
    if not mine:
        sender.text(phone, "Only a business's coordinator records its contribution."); return
    v = parse_rupees(amount)
    if not v:
        sender.text(phone, "Reply CONTRIBUTE and the amount in rupees, e.g. CONTRIBUTE 500."); return
    node = mine[0]
    try:
        core.contribute(con, node["id"], v * 100, member_id=m["id"])
    except core.PBError as e:
        sender.text(phone, err_text(e)); return
    rnd = core.current_round(con, node["parent_id"])
    sender.text(phone, f"Recorded: {node['name']} puts {rupees(v * 100)} into this round. The pool can now spend {rupees(core.spendable_budget(con, rnd))}. "
                       "Pay your share to the vendor when the item is bought; the treasurer pays the matched part.")


def nxt(phone: str, m: dict | None, node_id: str | None = None) -> None:
    if not m:
        return menu(phone, m)
    opts = [(n, r) for n, r in advanceable(con, m["id"]) if node_id in (None, n["id"])]
    if not opts:
        sender.text(phone, "Nothing to move on right now."); return
    if len(opts) > 1:
        sender.list(phone, "Which round?", "Choose", [(f"nx:{n['id']}", n["name"], NEXT_STEP[r["status"]]) for n, r in opts]); return
    node, rnd = opts[0]
    try:
        step = core.advance(con, rnd["id"])
    except core.PBError as e:
        sender.text(phone, err_text(e)); return
    from . import web
    web._announce(node["id"], step)
    rnd = core.get_round(con, rnd["id"])
    sender.text(phone, f"Done: {NEXT_STEP.get(rnd['status'], 'the round is ' + rnd['status'])}." if rnd["status"] in NEXT_STEP else f"Done. The round is {rnd['status']}.")


# --- treasurer --------------------------------------------------------------
def payouts(phone: str, m: dict | None) -> None:
    if not m or not is_treasurer(m):
        sender.text(phone, "Treasurer only."); return
    q = core.payout_queue(con)
    if not q:
        sender.text(phone, "Nothing to pay."); return
    for x in q:
        pa = x["payee_upi"] or x["lead_upi"]
        note = f"{x['round_id']} {x['title']}"
        text = f"{x['title']} · {rupees(x['amount_paise'])} · {x['kind']}\nto: {pa or 'no UPI id yet'}\nremark: {note}"
        if pa:
            text += f"\n{core.upi_link(pa, x['amount_paise'], note)}"
        sender.buttons(phone, text, [(f"paid:{x['id']}", "Mark paid")])
