"""Drive the WhatsApp adapter with Cloud API webhook bodies: an owner's card (coordinator + charter), a staff
link, self-registration of a new business, then a whole round as WhatsApp conversations; notifications inside
and outside the 24-hour window; the webhook's handshake, signature check and de-duplication."""
import hashlib
import hmac
import json
import os
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "w.sqlite")
os.environ["PB_SECRET"] = "test"
os.environ["PB_WA_NUMBER"] = "+91 12345 67890"
os.environ["PB_WA_POOL"] = "goa-biz"
os.environ["PB_WA_VERIFY_TOKEN"] = "verify-me"
os.environ["PB_WA_APP_SECRET"] = "app-secret"
os.environ["PB_WA_TOKEN"] = "graph-token"
os.environ["PB_WA_PHONE_ID"] = "1"
os.environ["PB_WA_TEMPLATE"] = "pb_notify"
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from publicbank import core, db, main, whatsapp  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

sent: list[tuple] = []   # (to, kind, body, extra)


class FakeSender:
    def text(self, to, body): sent.append((to, "text", body, None))
    def buttons(self, to, body, buttons): sent.append((to, "buttons", body, buttons))
    def list(self, to, body, button, rows): sent.append((to, "list", body, rows))
    def template(self, to, name, params): sent.append((to, "template", name, params))


whatsapp.sender = FakeSender()
con = whatsapp.con
N = 0


def payload(phone: str, text: str | None = None, button: str | None = None, name: str = "Asha", mid: str | None = None) -> dict:
    global N
    N += 1
    m = {"from": phone, "id": mid or f"wamid.{N}", "timestamp": "0"}
    if text is not None:
        m["type"] = "text"; m["text"] = {"body": text}
    else:
        m["type"] = "interactive"; m["interactive"] = {"type": "list_reply" if button.startswith(("rk:", "go:", "cmd:")) else "button_reply",
                                                       ("list_reply" if button.startswith(("rk:", "go:", "cmd:")) else "button_reply"): {"id": button, "title": "x"}}
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "contacts": [{"profile": {"name": name}, "wa_id": phone}], "messages": [m]}}]}]}


def last(phone):
    return [s for s in sent if s[0] == phone][-1]


def say(phone, text, name="Asha"):
    whatsapp.handle_update(payload(phone, text=text, name=name)); return last(phone)


def tap(phone, bid):
    whatsapp.handle_update(payload(phone, button=bid)); return last(phone)


def ids(extra):
    return [i for i, *_ in extra]


OWNER, STAFF1, STAFF2, CAFE, STRANGER = "919000000001", "919000000002", "919000000003", "919000000004", "919000000009"

# --- the pool, a pre-seeded business, an owner's card -----------------------------------------------------
pool = core.create_node(con, "goa-biz", "Goa Business Pool", kind="pool")
tre = core.upsert_member(con, verified_by="coordinator", telegram_id=7, display="treasurer")
core.join_node(con, "goa-biz", tre["id"], role="treasurer", auto_active=True)
core.create_node(con, "goa-biz/tailors", "Mandrem Tailors", kind="group", parent_id="goa-biz")
core.join_node(con, "goa-biz/tailors", tre["id"], role="treasurer", auto_active=True)
card = core.mint_token(con, "join_coord", node_id="goa-biz/tailors", ttl=core.TOKEN_TTL_JOIN)

# a stranger gets the menu, not an error
r = say(STRANGER, "hello")
assert r[1] == "text" and "join link" in r[2], r

# the owner scans the card: coordinator, then the charter
r = say(OWNER, f"JOIN {card}", name="Rekha")
assert r[1] == "buttons" and "Goa Business Pool works like this" in r[2] and ids(r[3]) == ["ch:goa-biz/tailors:y", "ch:goa-biz/tailors:n"], r
owner = whatsapp.member_for(OWNER)
assert owner and owner["verified_by"] == "whatsapp" and owner["display"] == "Rekha" and owner["wa_opt_in_at"], owner
assert core.is_role(con, "goa-biz/tailors", owner["id"], ("coordinator",))
# "Not now" keeps the node out of the pool; LINK asks again
r = tap(OWNER, "ch:goa-biz/tailors:n")
assert "Reply LINK" in r[2]
assert core.get_node(con, "goa-biz/tailors")["charter_at"] is None
r = say(OWNER, "link")
assert r[1] == "buttons" and ids(r[3])[0] == "ch:goa-biz/tailors:y"
r = tap(OWNER, "ch:goa-biz/tailors:y")
assert r[1] == "text" and "wa.me/911234567890?text=JOIN%20" in r[2], r
assert core.get_node(con, "goa-biz/tailors")["charter_at"]
staff_tok = r[2].split("JOIN%20", 1)[1].split()[0]

# staff tap the forwarded link: active at once; the owner is told
r = say(STAFF1, f"JOIN {staff_tok}", name="Sunil")
assert "You're in: Mandrem Tailors" in r[2], r
assert [s for s in sent if s[0] == OWNER][-1][2] == "Sunil joined Mandrem Tailors."
say(STAFF2, f"join {staff_tok}", name="Meena")
assert len(core.active_members(con, "goa-biz/tailors")) == 4   # treasurer, owner, two staff
# the owner tapping their own team link is not demoted
say(OWNER, f"JOIN {staff_tok}")
assert core.is_role(con, "goa-biz/tailors", owner["id"], ("coordinator",))
# an expired/unknown code
assert "expired" in say(STAFF1, "JOIN nope")[2]

# --- self-registration under the pool --------------------------------------------------------------------
r = say(CAFE, "JOIN", name="Joao")
assert "What's your business called" in r[2]
r = say(CAFE, "Sea Breeze Café")
assert r[1] == "buttons" and ids(r[3])[0] == "ch:goa-biz/sea-breeze-caf:y", r
cafe = core.get_node(con, "goa-biz/sea-breeze-caf")
assert cafe["name"] == "Sea Breeze Café" and cafe["parent_id"] == "goa-biz"
assert core.is_role(con, cafe["id"], whatsapp.member_for(CAFE)["id"], ("coordinator",))
tap(CAFE, f"ch:{cafe['id']}:y")
assert core.get_node(con, cafe["id"])["charter_at"]
# a second business with the same name gets a distinct id
say(STRANGER, "JOIN"); say(STRANGER, "Sea Breeze Cafe")
assert core.get_node(con, "goa-biz/sea-breeze-cafe")["name"] == "Sea Breeze Cafe"   # 'caf' vs 'cafe': distinct ids, no clash

# --- a round, entirely on WhatsApp ----------------------------------------------------------------------
rnd = core.open_round(con, "goa-biz/tailors", 100000 * 100)
r = say(STAFF1, "menu")
assert r[1] == "list" and "cmd:vote" in ids(r[3]) and "cmd:link" not in ids(r[3])
assert "cmd:link" in ids(say(OWNER, "menu")[3])

# propose: title → text → ideal → min → lead (button) → UPI → submit (button)
r = tap(STAFF1, "cmd:propose")
assert "What's it called" in r[2], r
say(STAFF1, "Shared delivery scooter")
say(STAFF1, "One scooter for the four shops on the lane; customers want home delivery.")
assert "whole number" in say(STAFF1, "lots")[2]
say(STAFF1, "₹60,000")
r = say(STAFF1, "40000")
assert r[1] == "buttons" and ids(r[3]) == ["pl:y", "pl:n"], r
r = tap(STAFF1, "pl:y")
assert "UPI id" in r[2]
assert "doesn't look like" in say(STAFF1, "not a upi")[2]
r = say(STAFF1, "sunil@okaxis")
assert r[1] == "buttons" and ids(r[3]) == ["pc:y", "pc:n"] and "You lead it" in r[2]
r = tap(STAFF1, "pc:y")
assert "Submitted" in r[2], r
props = core.eligible_proposals(con, rnd["id"])
assert len(props) == 1 and props[0]["title"] == "Shared delivery scooter" and whatsapp.member_for(STAFF1)["upi_id"] == "sunil@okaxis"
# a second item by the owner, who leads without a UPI step once one is stored
with db.tx(con): con.execute("UPDATE members SET upi_id=? WHERE id=?", ("rekha@upi", owner["id"]))
say(OWNER, "propose"); say(OWNER, "Billing software, one licence for all"); say(OWNER, "Shared GST billing subscription."); say(OWNER, "12000"); say(OWNER, "0")
r = tap(OWNER, "pl:y")
assert ids(r[3]) == ["pc:y", "pc:n"]
tap(OWNER, "pc:y")
assert len(core.eligible_proposals(con, rnd["id"])) == 2
# a stale button
assert "expired" in tap(OWNER, "pc:y")[2]

# the coordinator freezes (core, as the web page does); ballots
core.freeze_proposals(con, rnd["id"])
assert "Nothing to propose" in say(STAFF2, "propose")[2]
r = say(STAFF2, "vote")
assert "Split 100 points" in r[2] and "/l/" in r[2] and "?next=/n/goa-biz/tailors" in r[2], r
assert "add up to 90" in say(STAFF2, "60 30")[2]
r = say(STAFF2, "60 40")
assert r[1] == "buttons" and ids(r[3]) == ["bc:y", "bc:n"]
assert "Ballot saved" in tap(STAFF2, "bc:y")[2]
say(STAFF1, "VOTE"); say(STAFF1, "1:80 2:20"); tap(STAFF1, "bc:y")
say(OWNER, "vote"); say(OWNER, "30 70"); tap(OWNER, "bc:y")
assert len(core.ballots_of(con, rnd["id"])) == 3

# plans; rank by list taps
core.compute_plans(con, rnd["id"])
r = say(STAFF1, "rank")
assert r[1] == "list" and len(r[3]) == 4 and all(i.startswith("rk:") for i in ids(r[3])), r
assert "your share went" in sent[-2][2]
order = ids(r[3])
r = tap(STAFF1, order[0])
assert r[1] == "list" and len(r[3]) == 3
tap(STAFF1, order[1]); r = tap(STAFF1, order[2])
assert r[1] == "list" and len(r[3]) == 1
r = tap(STAFF1, order[3])
assert r[1] == "buttons" and ids(r[3]) == ["rc:y", "rc:n"], r
r = tap(STAFF1, "rc:n")
assert r[1] == "list" and len(r[3]) == 4
for i in order: tap(STAFF1, i)
assert "Ranking saved" in tap(STAFF1, "rc:y")[2]

# winner; approve by buttons: 60% of 4 eligible = 3
core.pick_winner(con, rnd["id"])
rnd = core.get_round(con, rnd["id"])
r = say(OWNER, "approve")
assert r[1] == "buttons" and ids(r[3]) == [f"ra:{rnd['winner_plan_id']}:approve", f"ra:{rnd['winner_plan_id']}:reject"] and "3 of 4 members" in r[2], r
for ph in (OWNER, STAFF1, STAFF2):
    assert "Approved" in tap(ph, f"ra:{rnd['winner_plan_id']}:approve")[2]
dec = core.close_ratification(con, rnd["id"], rnd["winner_plan_id"])
assert dec["verdict"] == "ratified" and dec["approvals"] == 3 and dec["eligible"] == 4, dec

# --- notifications: inside the 24h window → text; outside → template -------------------------------------
with db.tx(con): con.execute("UPDATE members SET wa_last_in_at=? WHERE wa_phone=?", (db.now() - 2 * 86400, STAFF2))
before = len(sent)
n = whatsapp.notify_node("goa-biz/tailors", "Ballots are open.")
assert n == 3, n   # owner + two staff; the treasurer has no WhatsApp
kinds = {s[0]: s[1] for s in sent[before:]}
assert kinds[OWNER] == "text" and kinds[STAFF1] == "text" and kinds[STAFF2] == "template", kinds
assert [s_ for s_ in sent[before:] if s_[0] == STAFF2][0][3] == ["Ballots are open."]

# --- the webhook: handshake, signature, de-duplication ----------------------------------------------------
body = json.dumps(payload(STAFF1, text="status", mid="wamid.dup")).encode()
sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
with TestClient(main.app) as c:
    assert c.get("/wa", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "abc123"}).text == "abc123"
    assert c.get("/wa", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"}).status_code == 403
    assert c.post("/wa", content=body, headers={"Content-Type": "application/json"}).status_code == 403
    before = len(sent)
    assert c.post("/wa", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig}).status_code == 200
    assert c.post("/wa", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig}).status_code == 200
    assert len(sent) == before + 1 and sent[-1][0] == STAFF1 and "Mandrem Tailors" in sent[-1][2], sent[-1]   # second delivery ignored
    # the login link the status message carries opens the member's page
    link = sent[-1][2].split("Open: ", 1)[1].strip()
    page = c.get(link.replace(whatsapp.BASE_URL, ""), follow_redirects=True)
    assert page.status_code == 200 and "Mandrem Tailors" in page.text
    # a wa.me poster for the owner's card
    assert c.get(f"/j/{card}/poster.png?via=wa").headers["content-type"] == "image/png"
    assert c.get("/health").json() == {"ok": True}

# --- treasurer flow on WhatsApp --------------------------------------------------------------------------
with db.tx(con): con.execute("UPDATE members SET wa_phone=?, wa_last_in_at=? WHERE id=?", ("919000000007", db.now(), tre["id"]))
r = say("919000000007", "payouts")
assert r[1] == "buttons" and ids(r[3])[0].startswith("paid:") and "upi://pay" in r[2], r
pid = ids(r[3])[0].split(":", 1)[1]
tap("919000000007", f"paid:{pid}")
assert "Recorded" in say("919000000007", "UTR123456789012")[2]
assert db.row(con.execute("SELECT status, reference FROM payouts WHERE id=?", (pid,)).fetchone())["reference"] == "UTR123456789012"
assert "Treasurer only" in say(STAFF1, "payouts")[2]

print("WHATSAPP OK · owner card → coordinator + charter · staff link · self-registration · propose/vote/rank/approve in-chat · "
      "window vs template · handshake, signature, dedupe · poster · treasurer paid ref")
